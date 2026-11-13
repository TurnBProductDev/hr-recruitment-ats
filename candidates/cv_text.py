"""Full CV text for Score Candidates.

The scorer used to see only the parsed profile fields and a ~1.5k-character
AI summary, which flattened very different CVs into the same score. This
reads the resume file itself and keeps its text on the candidate
(Candidate.cv_text) so it's only read once.

Where the file comes from, in order:
1. resume_blob_url - a CV uploaded through the app (web app storage).
2. resume_url when it points at this app's own Blob container (careers
   intake) - downloaded with a short-lived read-only SAS URL.
A legacy SharePoint link can't be read (no SharePoint access), so those
candidates are still scored on their parsed profile alone.

Never raises: no readable CV just means `None`, and scoring carries on
without it.
"""
import io
import logging
import re
import zipfile

import requests

from HR_management import pdf_text

from . import cv_storage

logger = logging.getLogger(__name__)

# Plenty for a normal CV (~2-4 pages) while keeping each scoring call's cost
# bounded - same cap CV parsing uses (candidates/cv_extraction.py).
MAX_CHARS = 12000
DOWNLOAD_TIMEOUT = 30


def _source(candidate):
    """(ref, loader) for the candidate's readable CV file, or (None, None)."""
    if candidate.resume_blob_url:
        name = candidate.resume_blob_url.name
        def load():
            with candidate.resume_blob_url.open('rb') as f:
                return f.read()
        return f'file:{name}', load
    url = candidate.resume_url or ''
    if cv_storage.is_our_blob_url(url):
        def load():
            resp = requests.get(cv_storage.sas_url(url, minutes=5), timeout=DOWNLOAD_TIMEOUT)
            resp.raise_for_status()
            return resp.content
        return f'blob:{url}', load
    return None, None


def _docx_text(content):
    """Plain text of a .docx without an extra dependency: paragraphs from
    word/document.xml."""
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        xml = z.read('word/document.xml').decode('utf-8', errors='ignore')
    xml = re.sub(r'</w:p>', '\n', xml)
    xml = re.sub(r'<w:tab/>', '\t', xml)
    text = re.sub(r'<[^>]+>', '', xml)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def _extract(content, ref):
    if content[:4] == b'PK\x03\x04' or ref.lower().endswith('.docx'):
        return _docx_text(content)
    return pdf_text.extract_text(content)  # '' for a scanned / image-only PDF


def get_cv_text(candidate, save=True):
    """The candidate's full CV text (capped), or None if no readable CV.
    Reuses the stored text unless the resume file has changed since."""
    ref, load = _source(candidate)
    if ref is None:
        return None
    if candidate.cv_text and candidate.cv_text_ref == ref:
        return candidate.cv_text
    try:
        text = _extract(load(), ref)
    except Exception as exc:  # noqa: BLE001 - an unreadable CV must not stop scoring
        logger.warning('Could not read the CV for candidate %s (%s): %s', candidate.pk, ref, exc)
        return None
    text = re.sub(r'[ \t]+', ' ', text or '').strip()[:MAX_CHARS]
    if not text:
        return None
    candidate.cv_text, candidate.cv_text_ref = text, ref[:1000]
    if save and candidate.pk:
        type(candidate).objects.filter(pk=candidate.pk).update(cv_text=text, cv_text_ref=ref[:1000])
    return text
