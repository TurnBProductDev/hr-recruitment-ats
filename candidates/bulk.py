"""Background processing for Bulk Upload CV.

Each uploaded file becomes a BulkUploadItem, and a worker thread walks the batch
one CV at a time: send it to the `cv-parse-single` Logic App, then create the
Candidate from what comes back. Failures are recorded on the item (no candidate
row is created) so the results screen can show "4 added, 1 failed" and offer a
retry.

A thread is enough here - the app runs as a single container and the batch is at
most a couple of dozen CVs. Progress lives in the database, not in memory, so a
restart mid-batch is visible on the results screen instead of silently lost.
"""
import logging
import os
import re
import threading
from datetime import timedelta

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import connection
from django.db.models import F
from django.utils import timezone

from . import cv_parser, services
from .cv_parser import CVParseError
from .models import BulkUploadBatch, BulkUploadItem

logger = logging.getLogger(__name__)

STATUS = BulkUploadItem.Status


def name_from_filename(filename):
    """Fallback candidate name when the CV itself has no readable name."""
    stem = os.path.basename(filename).rsplit('.', 1)[0]
    stem = re.sub(r'[_\-\.]+', ' ', stem)
    stem = re.sub(r'\b(cv|resume)\b', '', stem, flags=re.IGNORECASE).strip()
    return stem.title() or 'Unnamed Candidate'


def start_batch(batch):
    """Kick off (or resume) processing for a batch in the background."""
    thread = threading.Thread(target=_run_batch, args=(batch.pk,),
                              name=f'bulk-cv-{batch.pk}', daemon=True)
    thread.start()
    return thread


def _run_batch(batch_id):
    try:
        batch = BulkUploadBatch.objects.select_related('job').get(pk=batch_id)
    except BulkUploadBatch.DoesNotExist:
        return
    try:
        while True:
            item = batch.items.filter(status=STATUS.PENDING).order_by('pk').first()
            if item is None:
                break
            # process_item may find the row already claimed by another worker
            # (two Retry clicks); it returns None and we move on to the next.
            process_item(item, batch)
    finally:
        # Worker threads get their own DB connection; don't leak it.
        connection.close()


def process_item(item, batch=None):
    """Parse one CV and create its Candidate. Never raises - the outcome is
    always written back onto the item.

    Returns None if another worker already claimed this item, so a second Retry
    click can't parse the same CV twice and create two candidates.
    """
    batch = batch or item.batch
    claimed = BulkUploadItem.objects.filter(pk=item.pk, status=STATUS.PENDING).update(
        status=STATUS.PARSING, attempts=F('attempts') + 1, updated_at=timezone.now())
    if not claimed:
        return None
    item.refresh_from_db()

    try:
        with item.cv_file.open('rb') as fh:
            content = fh.read()

        data = cv_parser.parse_cv(
            # Unique but still readable in the SharePoint "Resume Received" folder.
            filename=f'{item.pk}_{item.filename}',
            content=content,
            role_hint=batch.job.title if batch.job else None,
            source_hint=batch.source,
        )
        fields, warning = cv_parser.map_to_candidate_fields(
            data, fallback_name=name_from_filename(item.filename))

        candidate = services.create_from_parsed_cv(
            fields, job=batch.job, source=batch.source,
            user=batch.created_by, performed_by=batch.performed_by,
            remarks='Created via Bulk Upload CV (CV parsed by Logic App)',
        )
        # Keep a copy of the file on the candidate itself, as the old bulk
        # upload did - resume_url points at the SharePoint copy.
        candidate.resume_blob_url.save(item.filename, ContentFile(content), save=True)

        item.candidate = candidate
        item.parsed_json = cv_parser.dump_response(data)
        item.warning = warning
        item.error_message = None
        item.status = STATUS.SUCCESS
    except CVParseError as exc:
        item.status = STATUS.ERROR
        item.error_message = str(exc)
    except Exception as exc:  # noqa: BLE001 - one bad CV must not stop the batch
        logger.exception('Bulk upload item %s failed', item.pk)
        item.status = STATUS.ERROR
        item.error_message = f'Unexpected error: {exc}'

    item.save()
    return item


def reap_stalled(batch):
    """Mark items left mid-parse by a restarted/crashed worker as failed, so the
    results screen stops waiting for them and offers a retry."""
    cutoff = timezone.now() - timedelta(
        seconds=2 * int(getattr(settings, 'CV_PARSER_TIMEOUT', 180)) + 60)
    return batch.items.filter(status=STATUS.PARSING, updated_at__lt=cutoff).update(
        status=STATUS.ERROR,
        error_message='Parsing was interrupted (the server restarted). Use Retry.',
        updated_at=timezone.now(),
    )


def summarise(batch):
    """Counts + per-item detail for the results screen and its poll endpoint."""
    reap_stalled(batch)
    items = list(batch.items.select_related('candidate').all())
    counts = {
        'total': len(items),
        'success': sum(1 for i in items if i.status == STATUS.SUCCESS),
        'error': sum(1 for i in items if i.status == STATUS.ERROR),
        'waiting': sum(1 for i in items if i.status in (STATUS.PENDING, STATUS.PARSING)),
    }
    counts['done'] = counts['success'] + counts['error']
    counts['finished'] = counts['waiting'] == 0
    return items, counts
