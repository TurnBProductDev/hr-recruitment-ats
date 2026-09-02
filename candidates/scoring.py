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
from django.utils import timezone

from . import match_scoring
from .match_scoring import ScoreError
from .models import Candidate

logger = logging.getLogger(__name__)

MS = Candidate.MatchState
# A candidate is "pending" if never scored, or the last attempt failed.
PENDING_STATES = (MS.PENDING, MS.ERROR)


def start_job_scoring(job):
    """Kick off (or resume) scoring for every pending candidate on `job`."""
    thread = threading.Thread(target=_run, args=(job.pk,),
                              name=f'score-job-{job.pk}', daemon=True)
    thread.start()
    return thread


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
