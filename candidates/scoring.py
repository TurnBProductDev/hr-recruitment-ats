"""Background processing for Score Candidates.

One job (vacancy) is scored at a time: a worker thread walks every candidate
mapped to it that isn't scored yet, calling match_scoring.score_candidate() for
each and writing the result straight onto the Candidate row. Progress lives on
the candidate rows themselves (match_state), not in memory, so a restart
mid-run is visible on the progress page instead of silently lost - same design
as candidates/bulk.py's CV parsing worker.
"""
import logging
import threading
from datetime import timedelta

from django.conf import settings
from django.db import connection
from django.db.models import Q
from django.utils import timezone

from . import match_scoring, profile_extraction
from .match_scoring import ScoreError
from .models import Candidate

logger = logging.getLogger(__name__)

MS = Candidate.MatchState
STATUS = Candidate.Status
# A candidate is "pending" if never scored, or the last attempt failed.
PENDING_STATES = (MS.PENDING, MS.ERROR)

# Scope for "Rescore Everyone" (the Scoring Criteria page's rescore action)
# and candidates.management.commands.rescore_candidates --pool all: every
# candidate who's an active application right now - past screening and
# still live (Active Pool), or freshly Open. Defined once here so the web
# action and the CLI command read from the same definition instead of two
# copies that could drift out of sync.
_INITIAL_HOLD = Q(status=STATUS.SCREENING_HOLD, hold_from_status=STATUS.OPEN)
ACTIVE_POOL = Q(status__in=(STATUS.SHORTLISTED, STATUS.ROUND1, STATUS.INTERVIEW,
                            STATUS.FINAL_SELECTION)) | (Q(status=STATUS.SCREENING_HOLD) & ~_INITIAL_HOLD)
OPEN_APPLICATIONS = Q(status=STATUS.OPEN)


def start_job_scoring(job):
    """Kick off (or resume) scoring for every pending candidate on `job`."""
    thread = threading.Thread(target=_run, args=(job.pk,),
                              name=f'score-job-{job.pk}', daemon=True)
    thread.start()
    return thread


def start_bulk_rescore(job=None):
    """Force re-score every Active Pool / Open Applications candidate in the
    background - scoped to one role when `job` is given (the Scoring
    Criteria page's "Rescore This Role" action, since criteria are per-role
    now - see candidates.models.ScoringCriteria), or every role otherwise
    (General Application excluded either way, since it's never mapped to a
    role - nothing to score against; still used with no job by
    candidates.management.commands.rescore_candidates --pool all).

    Used after a role's criteria text is edited, since an already-DONE
    score doesn't refresh on its own (score_one() only claims
    PENDING_STATES).

    Resets match_state to PENDING for the whole scope up front, then scores
    a fixed snapshot of those candidates one at a time - unlike _run(job_id)
    below, this doesn't keep re-querying "whatever is still pending", so a
    candidate that fails and lands back in ERROR (still a PENDING_STATE)
    can't make the run loop on it forever; every candidate in the batch is
    attempted exactly once. Returns how many candidates were queued."""
    from .views import GENERAL_APPLICATION  # local import - views.py imports this module
    not_general = ~Q(job__isnull=True) & ~Q(job__title__iexact=GENERAL_APPLICATION)
    qs = Candidate.objects.filter(ACTIVE_POOL | OPEN_APPLICATIONS).filter(not_general)
    if job is not None:
        qs = qs.filter(job=job)

    pks = list(qs.values_list('pk', flat=True))
    Candidate.objects.filter(pk__in=pks).update(match_state=MS.PENDING)

    thread_name = f'rescore-job-{job.pk}' if job is not None else 'rescore-all'
    thread = threading.Thread(target=_run_scope, args=(pks,), name=thread_name, daemon=True)
    thread.start()
    return len(pks)


def _run_scope(pks):
    try:
        for pk in pks:
            candidate = (Candidate.objects.filter(pk=pk, match_state__in=PENDING_STATES)
                        .select_related('job').first())
            if candidate is not None:
                score_one(candidate)
    finally:
        connection.close()


def start_one(candidate):
    """Kick off (or resume) scoring for exactly one candidate in the
    background - the profile page's manual Re-score action. The caller is
    responsible for resetting match_state to PENDING first if it should
    re-run a candidate that's already DONE; score_one() only claims rows
    still in PENDING_STATES."""
    thread = threading.Thread(target=_run_one, args=(candidate.pk,),
                              name=f'score-candidate-{candidate.pk}', daemon=True)
    thread.start()
    return thread


def _run_one(candidate_id):
    try:
        candidate = Candidate.objects.select_related('job').get(pk=candidate_id)
        score_one(candidate)
    except Candidate.DoesNotExist:
        pass
    finally:
        connection.close()


def _run(job_id):
    try:
        while True:
            candidate = (Candidate.objects.filter(job_id=job_id, match_state__in=PENDING_STATES)
                        .order_by('pk').first())
            if candidate is None:
                break
            score_one(candidate)
    finally:
        # Worker threads get their own DB connection; don't leak it.
        connection.close()


def score_one(candidate):
    """Score one candidate against their mapped job. Never raises - the
    outcome is always written back onto the candidate.

    Returns None if another worker already claimed this row.
    """
    claimed = Candidate.objects.filter(pk=candidate.pk, match_state__in=PENDING_STATES).update(
        match_state=MS.SCORING, updated_at=timezone.now())
    if not claimed:
        return None
    candidate.refresh_from_db()

    # The careers-mailbox intake (sp_intake_add_candidate) never fills
    # last_role/last_company/experience/skills - see candidates/profile_extraction.py.
    # Backfill from the AI CV Summary before scoring so those blanks don't
    # understate the match score.
    profile_extraction.apply_missing_fields(candidate)

    try:
        result = match_scoring.score_candidate(candidate, candidate.job)
        candidate.match_score = result['score']
        candidate.match_breakdown = match_scoring.dump_breakdown(result)
        candidate.match_rationale = result.get('rationale')
        candidate.match_state = MS.DONE
        candidate.match_error = None
        candidate.match_scored_at = timezone.now()
    except ScoreError as exc:
        candidate.match_state = MS.ERROR
        candidate.match_error = str(exc)
    except Exception as exc:  # noqa: BLE001 - one bad candidate must not stop the run
        logger.exception('Scoring candidate %s failed', candidate.pk)
        candidate.match_state = MS.ERROR
        candidate.match_error = f'Unexpected error: {exc}'

    candidate.save(update_fields=[
        'match_score', 'match_breakdown', 'match_rationale',
        'match_state', 'match_error', 'match_scored_at', 'updated_at',
    ])
    return candidate


def reap_stalled(job_id):
    """Mark rows left mid-score by a restarted/crashed worker as failed, so the
    progress page stops waiting for them and offers a retry."""
    cutoff = timezone.now() - timedelta(
        seconds=2 * int(getattr(settings, 'SCORE_CANDIDATES_TIMEOUT', 60)) + 60)
    return Candidate.objects.filter(job_id=job_id, match_state=MS.SCORING, updated_at__lt=cutoff).update(
        match_state=MS.ERROR,
        match_error='Scoring was interrupted (the server restarted). Click Score again to retry.',
        updated_at=timezone.now(),
    )


def summarise(job_id):
    """Counts for the progress page and its poll endpoint."""
    reap_stalled(job_id)
    counts = {
        'total': 0, 'done': 0, 'error': 0, 'waiting': 0,
    }
    for row in Candidate.objects.filter(job_id=job_id).values('match_state'):
        counts['total'] += 1
        if row['match_state'] == MS.DONE:
            counts['done'] += 1
        elif row['match_state'] == MS.ERROR:
            counts['error'] += 1
        else:
            counts['waiting'] += 1
    # 'processed' (scored + failed) is what the progress bar fills against -
    # 'done' alone would stall the bar short of 100% on any run with a failure.
    counts['processed'] = counts['done'] + counts['error']
    counts['finished'] = counts['waiting'] == 0
    return counts


def pending_count(job_id):
    return Candidate.objects.filter(job_id=job_id, match_state__in=PENDING_STATES).count()
