"""Reads an uploaded JD file and returns clean Description/Requirements text
to pre-fill the Vacancy form - the same "read the file directly with one
Azure OpenAI call" pattern as candidates/cv_extraction.py, reusing its PDF
reading (HR_management/pdf_text.py) and its Azure OpenAI deployment.

This matters beyond convenience: Score Candidates reads Job.description/
Job.requirements as the JD text it scores every candidate against (see
candidates/match_scoring.py) - a vacancy saved with those fields blank means
every score for that role is only ever judged against the bare job title.
Auto-filling them from the JD the moment it's uploaded means a real JD text
actually exists to score against, without relying on someone remembering to
type it in by hand.
"""
import json
import logging

import requests
from django.conf import settings

from HR_management import pdf_text
from prompts.jd_extraction import RESPONSE_JSON_SCHEMA, SYSTEM_PROMPT

logger = logging.getLogger(__name__)

MIN_TEXT_CHARS = 200  # below this, treat the PDF as scanned and fall back to vision


class JDExtractionError(Exception):
    """Raised when a JD file could not be read at all. Message is shown to HR."""


def is_configured():
    return bool(getattr(settings, 'AZURE_OPENAI_ENDPOINT', '') and getattr(settings, 'AZURE_OPENAI_KEY', ''))


def _endpoint_url():
    endpoint = settings.AZURE_OPENAI_ENDPOINT.rstrip('/')
    deployment = settings.AZURE_OPENAI_SCORING_DEPLOYMENT
    api_version = settings.AZURE_OPENAI_API_VERSION
    return f'{endpoint}/openai/deployments/{deployment}/chat/completions?api-version={api_version}'


def extract_fields(content, title_hint=None):
    """Read a JD file (raw bytes) and return {'description': str,
    'requirements': str}. Raises JDExtractionError (message fit for the
    upload UI) only when nothing could be read at all - either field coming
    back blank on an otherwise successful read is not an error."""
    if not is_configured():
        raise JDExtractionError(
            'JD reading is not configured - set AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_KEY.')

    text = pdf_text.extract_text(content)
    page_images = []
    if len(text) < MIN_TEXT_CHARS:
        try:
            page_images = pdf_text.render_page_images(content)
        except Exception as exc:  # noqa: BLE001 - fall through with whatever text we have
            logger.warning('Could not render JD pages to images: %s', exc)
        if not text and not page_images:
            raise JDExtractionError('Could not read this file - it may be corrupted.')

    user_parts = []
    if title_hint:
        user_parts.append(f'Job Title: {title_hint}')
    content_block = [{'type': 'text', 'text': ('\n\n'.join(user_parts) + '\n\nJD file text:\n\n' + text)
                       if user_parts else (text or 'The text layer was unreadable - read the attached page images instead.')}]
    for image_bytes in page_images:
        import base64
        b64 = base64.b64encode(image_bytes).decode('ascii')
        content_block.append({'type': 'image_url', 'image_url': {'url': f'data:image/png;base64,{b64}'}})

    payload = {
        'messages': [
            {'role': 'system', 'content': SYSTEM_PROMPT},
            {'role': 'user', 'content': content_block},
        ],
        'temperature': 0,
        'max_tokens': 1200,
        'response_format': {'type': 'json_schema', 'json_schema': RESPONSE_JSON_SCHEMA},
    }
    timeout = int(getattr(settings, 'SCORE_CANDIDATES_TIMEOUT', 60))

    try:
        response = requests.post(
            _endpoint_url(),
            headers={'api-key': settings.AZURE_OPENAI_KEY, 'Content-Type': 'application/json'},
            json=payload, timeout=(10, timeout),
        )
    except requests.Timeout:
        raise JDExtractionError(f'Azure OpenAI did not respond within {timeout}s.')
    except requests.RequestException as exc:
        raise JDExtractionError(f'Could not reach Azure OpenAI: {exc}')

    if response.status_code >= 400:
        raise JDExtractionError(f'Azure OpenAI returned HTTP {response.status_code}: {response.text[:300]}')

    try:
        data = json.loads(response.json()['choices'][0]['message']['content'])
    except (ValueError, KeyError, IndexError):
        raise JDExtractionError('Azure OpenAI returned an unexpected response shape.')

    return {
        'description': (data.get('description') or '').strip()[:8000],
        'requirements': (data.get('requirements') or '').strip()[:8000],
    }
