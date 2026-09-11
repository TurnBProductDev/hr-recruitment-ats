"""
Force re-score candidates against their mapped vacancy with the current
match_scoring rubric, even if they already have a score from an earlier
version of it (e.g. the pre-holistic weighted-rubric scorer).

Scope (--pool):
    active_pool  Everyone past screening and still live: Qualified, Round 1,
                 Round 2, Final Selection, or a later-stage Hold - same
                 membership as the dashboard's "Active Pool" KPI
                 (dashboard.views.SHORTLISTED_GROUP).
    open         The Open Applications tab (status=OPEN).
    all          Both of the above (default).

General Application candidates are always excluded - they aren't mapped to a
role, so there's nothing to score them against (see
candidates.views.ScoreCandidatesView).

Usage:
    python manage.py rescore_candidates                    # active_pool + open
    python manage.py rescore_candidates --pool active_pool
    python manage.py rescore_candidates --pool open
    python manage.py rescore_candidates --dry-run
    python manage.py rescore_candidates --limit 20          # test on a few first
"""
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from candidates import match_scoring, scoring
from candidates.models import Candidate
from candidates.views import GENERAL_APPLICATION

STATUS = Candidate.Status
MS = Candidate.MatchState

# Same membership as dashboard.views.SHORTLISTED_GROUP ("Active Pool"): every
# status past screening, plus a hold taken at any later stage - not a hold
# taken before screening (that's Future Prospects, not an active application).
INITIAL_HOLD = Q(status=STATUS.SCREENING_HOLD, hold_from_status=STATUS.OPEN)
ACTIVE_POOL = Q(status__in=(STATUS.SHORTLISTED, STATUS.ROUND1, STATUS.INTERVIEW,
                            STATUS.FINAL_SELECTION)) | (Q(status=STATUS.SCREENING_HOLD) & ~INITIAL_HOLD)
OPEN_APPLICATIONS = Q(status=STATUS.OPEN)

POOL_FILTERS = {
    'active_pool': ACTIVE_POOL,
    'open': OPEN_APPLICATIONS,
    'all': ACTIVE_POOL | OPEN_APPLICATIONS,
}

NOT_GENERAL_APPLICATION = ~Q(job__isnull=True) & ~Q(job__title__iexact=GENERAL_APPLICATION)


class Command(BaseCommand):
    help = 'Force re-score candidates (Active Pool and/or Open Applications) with the current scoring rubric.'

    def add_arguments(self, parser):
        parser.add_argument('--pool', choices=list(POOL_FILTERS), default='all',
                            help='Which candidates to re-score (default: all = active_pool + open).')
        parser.add_argument('--dry-run', action='store_true',
                            help='Report how many candidates would be re-scored; call nothing.')
        parser.add_argument('--limit', type=int, default=None,
                            help='Process at most this many candidates (useful for a first test run).')

    def handle(self, *args, **options):
        if not match_scoring.is_configured():
            raise CommandError('Scoring is not configured - set AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_KEY.')

        queryset = (Candidate.objects.filter(POOL_FILTERS[options['pool']])
                    .filter(NOT_GENERAL_APPLICATION).select_related('job').order_by('pk'))
        total = queryset.count()
        if options['limit']:
            queryset = queryset[:options['limit']]

        self.stdout.write(f'{total} candidate(s) in scope (--pool {options["pool"]}).')
        if options['dry_run']:
            self.stdout.write('Dry run - not calling Azure OpenAI.')
            return

        # score_one() only claims rows still PENDING/ERROR - reset every
        # targeted row first so an already-DONE score (from an older rubric
        # version) gets picked up and overwritten too.
        candidates = list(queryset)
        Candidate.objects.filter(pk__in=[c.pk for c in candidates]).update(match_state=MS.PENDING)

        done_count, error_count = 0, 0
        for i, candidate in enumerate(candidates, start=1):
            candidate.match_state = MS.PENDING
            result = scoring.score_one(candidate)
            if result and result.match_state == MS.DONE:
                done_count += 1
                self.stdout.write(f'  [{i}/{len(candidates)}] #{candidate.pk} {candidate.full_name}: '
                                  f'{result.match_score} ({candidate.job.title})')
            else:
                error_count += 1
                err = result.match_error if result else 'already claimed by another run'
                self.stdout.write(self.style.WARNING(
                    f'  [{i}/{len(candidates)}] #{candidate.pk} {candidate.full_name}: FAILED - {err}'))

        self.stdout.write(self.style.SUCCESS(f'Done. Scored {done_count} candidate(s); {error_count} failed.'))
