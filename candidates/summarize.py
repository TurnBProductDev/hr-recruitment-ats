"""Background processing for regenerating one candidate's AI CV summary.

A single-candidate action, not a batch: HR can re-read an existing
candidate's already-stored resume - after correcting their details, or
after the summary prompt/model changes - without re-uploading the file.
Mirrors scoring.py's/bulk.py's thread-per-run pattern, sized to one row.
The caller (the view) does the atomic claim (IDLE/ERROR -> RUNNING) before
starting the thread, so the profile page sees "Regenerating" immediately
on redirect instead of racing the thread for it.
"""
import logging
import threading

from django.db import connection

from . import cv_parser
from .cv_parser import CVParseError
from .models import Candidate

logger = logging.getLogger(__name__)

CSS = Candidate.CVSummaryState


def start(candidate):
    """Kick off summary regeneration for one candidate in the background."""
    thread = threading.Thread(target=_run, args=(candidate.pk,),
                              name=f'cv-summary-{candidate.pk}', daemon=True)
    thread.start()
    return thread


def _run(candidate_id):
    try:
        regenerate(candidate_id)
    finally:
        connection.close()


def regenerate(candidate_id):
    """Re-read one candidate's stored resume and refresh cv_summary. Never
    raises - the outcome is always written back onto the candidate.
    """
    try:
        candidate = Candidate.objects.select_related('job').get(pk=candidate_id)
    except Candidate.DoesNotExist:
        return None

    try:
        if not candidate.resume_blob_url:
            raise CVParseError(
                "This candidate's resume was linked via URL, not uploaded, "
                "so there's no stored file to re-read.")
        with candidate.resume_blob_url.open('rb') as fh:
            content = fh.read()
        filename = candidate.resume_blob_url.name.rsplit('/', 1)[-1] or f'{candidate.pk}.pdf'

        data = cv_parser.parse_cv(
            filename=f'{candidate.pk}_{filename}',
            content=content,
            role_hint=candidate.job.title if candidate.job else None,
            source_hint=candidate.source,
            # Re-reading an already-filed CV shouldn't file a second copy.
            upload_to_sharepoint=False,
        )
        fields, _warning = cv_parser.map_to_candidate_fields(data, fallback_name=candidate.full_name)
        new_summary = fields.get('cv_summary')
        if not new_summary:
            raise CVParseError('The CV was re-read but no summary was produced.')

        candidate.cv_summary = new_summary
        candidate.cv_summary_state = CSS.IDLE
        candidate.cv_summary_error = None
    except CVParseError as exc:
        candidate.cv_summary_state = CSS.ERROR
        candidate.cv_summary_error = str(exc)
    except Exception as exc:  # noqa: BLE001 - must never crash the worker thread
        logger.exception('CV summary regeneration failed for candidate %s', candidate.pk)
        candidate.cv_summary_state = CSS.ERROR
        candidate.cv_summary_error = f'Unexpected error: {exc}'

    candidate.save(update_fields=['cv_summary', 'cv_summary_state', 'cv_summary_error', 'updated_at'])
    return candidate
