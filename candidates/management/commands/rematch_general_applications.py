"""
Re-check candidates currently sitting in General Application against the
vacancies open right now, using the AI job matcher (candidates/job_matching.py) -
for applications that arrived before a matching vacancy existed, or that
intake's own matching (exact title match, or the AI match at intake time -
see prompts/cv_extraction.py's matched_job_title) missed. Moving a match
uses the exact same steps as the manual "Change Vacancy" action
(CandidateChangeJobView): re-point job, clear the now-stale match score, and
log a Note - so it looks the same in the candidate's history either way.

Usage:
    python manage.py rematch_general_applications              # run for real
    python manage.py rematch_general_applications --dry-run    # just report matches
    python manage.py rematch_general_applications --limit 20   # test on a few first
"""
from django.core.management.base import BaseCommand, CommandError

from candidates import job_matching
from candidates.models import Candidate, Note
from candidates.views import GENERAL_APPLICATION
from jobs.models import Job


def _pending_candidates():
    return (Candidate.objects.filter(job__title__iexact=GENERAL_APPLICATION)
            .exclude(role_applied__isnull=True).exclude(role_applied__exact='')
            .select_related('job').order_by('pk'))


class Command(BaseCommand):
    help = 'Re-check General Application candidates against currently open vacancies.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Report what would be matched; call nothing.')
        parser.add_argument('--limit', type=int, default=None,
                            help='Process at most this many candidates (useful for a first test run).')

    def handle(self, *args, **options):
        if not job_matching.is_configured():
            raise CommandError('Job matching is not configured - set AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_KEY.')

        open_jobs = {j.title: j for j in Job.objects.filter(status=Job.Status.OPEN, is_archived=False)}
        if not open_jobs:
            self.stdout.write('No open vacancies right now - nothing to match against.')
            return

        queryset = _pending_candidates()
        total = queryset.count()
        if options['limit']:
            queryset = queryset[:options['limit']]

        self.stdout.write(f'{total} candidate(s) in General Application with a stated role applied.')
        self.stdout.write(f'Open vacancies: {", ".join(sorted(open_jobs))}')
        if options['dry_run']:
            self.stdout.write('Dry run - not calling Azure OpenAI.')

        matched_count, error_count = 0, 0
        for candidate in queryset:
            try:
                title = job_matching.match_job_title(candidate, list(open_jobs))
            except job_matching.JobMatchError as exc:
                error_count += 1
                self.stdout.write(self.style.WARNING(
                    f'  #{candidate.pk} {candidate.full_name}: FAILED - {exc}'))
                continue

            if not title:
                continue
            matched_count += 1
            job = open_jobs[title]
            self.stdout.write(
                f'  #{candidate.pk} {candidate.full_name} (applied: {candidate.role_applied!r}) -> {job.title}')
            if options['dry_run']:
                continue

            candidate.job = job
            candidate.match_score = None
            candidate.match_breakdown = None
            candidate.match_rationale = None
            candidate.match_error = None
            candidate.match_scored_at = None
            candidate.match_state = Candidate.MatchState.PENDING
            candidate.save(update_fields=['job', 'updated_at', 'match_score', 'match_breakdown',
                                          'match_rationale', 'match_error', 'match_scored_at', 'match_state'])
            Note.objects.create(
                candidate=candidate,
                text=f'Vacancy changed: General Application -> {job.title} (AI re-match backfill)')

        self.stdout.write(self.style.SUCCESS(
            f'Done. {matched_count} matched out of {min(total, options["limit"] or total)} checked; '
            f'{error_count} failed.'))
