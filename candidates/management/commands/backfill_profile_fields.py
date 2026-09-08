"""
Backfill qualification/last_role/last_company/total_experience_years/skills on
existing candidates from their AI CV Summary.

Both intake pipelines (careers-mailbox intake via sp_intake_add_candidate, and
Bulk Upload CV) only extract what the shared Form Recognizer model is labelled
for - Name, Email, Mobile, Education - see logic_apps/README.md. Candidates
created before candidates/profile_extraction.py existed (or scored candidates
that were scored before this backfill was added inline in scoring.score_one)
can be sitting with those fields blank even though their cv_summary already
describes them. This command sweeps the whole table once.

Usage:
    python manage.py backfill_profile_fields              # run for real
    python manage.py backfill_profile_fields --dry-run     # just report counts
    python manage.py backfill_profile_fields --limit 20    # test on a few first
"""
from django.core.management.base import BaseCommand
from django.db.models import Q

from candidates import profile_extraction
from candidates.models import Candidate


def _pending_candidates():
    """Candidates with an AI CV Summary and at least one target field blank."""
    blank = Q()
    for field in profile_extraction.TARGET_FIELDS:
        blank |= Q(**{f'{field}__isnull': True})
        if field != 'total_experience_years':  # a DecimalField - '' isn't a valid lookup value
            blank |= Q(**{field: ''})
    return (Candidate.objects.exclude(cv_summary__isnull=True).exclude(cv_summary='')
            .filter(blank).order_by('pk'))


class Command(BaseCommand):
    help = 'Backfill missing profile fields on existing candidates from their AI CV Summary.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Report how many candidates would be processed; call nothing.')
        parser.add_argument('--limit', type=int, default=None,
                            help='Process at most this many candidates (useful for a first test run).')

    def handle(self, *args, **options):
        if not profile_extraction.is_configured():
            self.stderr.write(self.style.ERROR(
                'Profile extraction is not configured - set AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_KEY.'))
            return

        queryset = _pending_candidates()
        total = queryset.count()
        if options['limit']:
            queryset = queryset[:options['limit']]

        self.stdout.write(f'{total} candidate(s) have a CV summary with at least one blank field.')
        if options['dry_run']:
            self.stdout.write('Dry run - not calling Azure OpenAI.')
            return

        filled_count, skipped_count = 0, 0
        for candidate in queryset:
            filled = profile_extraction.apply_missing_fields(candidate)
            if filled:
                filled_count += 1
                self.stdout.write(f'  #{candidate.pk} {candidate.full_name}: filled {", ".join(filled)}')
            else:
                skipped_count += 1

        self.stdout.write(self.style.SUCCESS(
            f'Done. Filled at least one field for {filled_count} candidate(s); '
            f'{skipped_count} produced nothing new (summary did not clearly state it, or extraction failed).'))
