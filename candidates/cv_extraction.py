"""Reads a CV directly and returns every field the ATS knows how to store, in
one Azure OpenAI call - replacing Form Recognizer + the Logic App's separate
extract/summary calls for Bulk Upload CV.

Text comes straight out of the PDF's own text layer (PyMuPDF, no network
call) for the normal case. A CV with no usable text layer (a scanned/
photographed resume) falls back to rendering its pages to images and letting
the vision-capable deployment read them directly - see _read_cv_text.

Only one Azure OpenAI call either way: unlike the old pipeline (Form
Recognizer's 4 labelled fields -> a narrow extraction call -> a separate
summary call -> profile_extraction's separate backfill call), this asks for
every Candidate field - including Skills and a structured Experience list -
in the same response as the summary.
"""
import base64
import datetime
import json
import logging
import re

import requests
from django.conf import settings

from HR_management import pdf_text
from prompts.cv_extraction import RESPONSE_JSON_SCHEMA, SYSTEM_PROMPT

logger = logging.getLogger(__name__)

MIN_TEXT_CHARS = 200  # below this, treat the PDF as scanned and fall back to vision
MAX_TEXT_CHARS = 12000  # mirrors the old Logic App's own cap on CV text sent to the model

_NULLISH = ('null', 'none', 'n/a', 'not found', 'not mentioned', 'not stated', '')


class CVExtractionError(Exception):
    """Raised when a CV could not be read at all. Message is shown to HR."""


def is_configured():
    return bool(getattr(settings, 'AZURE_OPENAI_ENDPOINT', '') and getattr(settings, 'AZURE_OPENAI_KEY', ''))


def _endpoint_url():
    endpoint = settings.AZURE_OPENAI_ENDPOINT.rstrip('/')
    deployment = settings.AZURE_OPENAI_SCORING_DEPLOYMENT
    api_version = settings.AZURE_OPENAI_API_VERSION
    return f'{endpoint}/openai/deployments/{deployment}/chat/completions?api-version={api_version}'


def _clean(value, limit=None):
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or value.lower() in _NULLISH:
        return None
    return value[:limit] if limit else value


def _clean_decimal(value):
    try:
        return round(float(value), 1) if value is not None else None
    except (TypeError, ValueError):
        return None


_MONTH_RE = re.compile(r'^(\d{4})-(\d{1,2})(?:-(\d{1,2}))?$')


def _clean_date(value):
    """Best-effort 'YYYY', 'YYYY-MM' or 'YYYY-MM-DD' -> date. Never raises -
    an odd date from the model just comes back None rather than blocking the
    whole candidate from being created."""
    if not isinstance(value, str) or not value.strip():
        return None
    value = value.strip()
    match = _MONTH_RE.match(value)
    if match:
        year, month, day = match.groups()
        try:
            return datetime.date(int(year), int(month), int(day) if day else 1)
        except ValueError:
            return None
    if re.match(r'^\d{4}$', value):
        return datetime.date(int(value), 1, 1)
    return None


def _clean_experience(raw_list):
    entries = []
    for item in (raw_list or [])[:30]:
        if not isinstance(item, dict):
            continue
        company = _clean(item.get('company_name'), 255)
        if not company:
            continue
        entries.append({
            'company_name': company,
            'designation': _clean(item.get('designation'), 255),
            'start_date': _clean_date(item.get('start_date')),
            'end_date': _clean_date(item.get('end_date')),
            'skills': _clean(item.get('skills'), 500),
        })
    return entries


def _build_messages(cv_text, page_images):
    content = [{'type': 'text', 'text': f'CV text:\n\n{cv_text}' if cv_text else
                'The CV text layer was unreadable - read the attached page images instead.'}]
    for image_bytes in page_images:
        b64 = base64.b64encode(image_bytes).decode('ascii')
        content.append({'type': 'image_url', 'image_url': {'url': f'data:image/png;base64,{b64}'}})
    return [
        {'role': 'system', 'content': SYSTEM_PROMPT},
        {'role': 'user', 'content': content},
    ]


def extract_profile(content, role_hint=None, source_hint=None, email_context=None):
    """Read a CV (raw PDF bytes) and return a dict of Candidate-shaped fields
    plus an 'experience' list of CandidateExperience-shaped dicts.

    role_hint/source_hint are pre-known values (Bulk Upload CV: the vacancy/
    source HR already picked on the upload screen) - a steer, not something
    to re-derive. email_context is the opposite: raw, un-interpreted context
    (the careers-mailbox intake's email Subject + Body) for the model to
    infer role_applied/source *from*, the way Extract_completion's prompt
    used to.

    Raises CVExtractionError (message fit for the results screen) only when
    nothing could be read at all - a partially-empty result is not an error,
    same as the old Logic App contract."""
    if not is_configured():
        raise CVExtractionError(
            'CV reading is not configured - set AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_KEY.')

    cv_text = pdf_text.extract_text(content)[:MAX_TEXT_CHARS]
    page_images = []
    if len(cv_text) < MIN_TEXT_CHARS:
        try:
            page_images = pdf_text.render_page_images(content)
        except Exception as exc:  # noqa: BLE001 - fall through with whatever text we have
            logger.warning('Could not render CV pages to images: %s', exc)
        if not cv_text and not page_images:
            raise CVExtractionError('Could not read this CV - the file may be corrupted.')

    hint_lines = []
    if email_context:
        hint_lines.append(email_context)
    if role_hint:
        hint_lines.append(f'Vacancy applied to (from the upload/email context): {role_hint}')
    if source_hint:
        hint_lines.append(f'Application source: {source_hint}')
    messages = _build_messages(
        ('\n'.join(hint_lines) + '\n\n' + cv_text) if hint_lines else cv_text, page_images)

    payload = {
        'messages': messages,
        'temperature': 0,
        'max_tokens': 1800,
        'response_format': {'type': 'json_schema', 'json_schema': RESPONSE_JSON_SCHEMA},
    }
    timeout = int(getattr(settings, 'CV_PARSER_TIMEOUT', 180))

    try:
        response = requests.post(
            _endpoint_url(),
            headers={'api-key': settings.AZURE_OPENAI_KEY, 'Content-Type': 'application/json'},
            json=payload, timeout=(10, timeout),
        )
    except requests.Timeout:
        raise CVExtractionError(f'Azure OpenAI did not respond within {timeout}s.')
    except requests.RequestException as exc:
        raise CVExtractionError(f'Could not reach Azure OpenAI: {exc}')

    if response.status_code >= 400:
        raise CVExtractionError(f'Azure OpenAI returned HTTP {response.status_code}: {response.text[:300]}')

    try:
        raw = json.loads(response.json()['choices'][0]['message']['content'])
    except (ValueError, KeyError, IndexError):
        raise CVExtractionError('Azure OpenAI returned an unexpected response shape.')

    email = (_clean(raw.get('email')) or '').lower().replace(' ', '')

    return {
        'full_name': _clean(raw.get('name'), 255),
        'email': email or None,
        'phone': _clean(raw.get('mobile'), 20),
        'dob': _clean_date(raw.get('dob')),
        'current_location': _clean(raw.get('current_location'), 255),
        'linkedin': _clean(raw.get('linkedin'), 200),
        'portfolio_url': _clean(raw.get('portfolio_url'), 200),
        'qualification': _clean(raw.get('qualification'), 255),
        'last_role': _clean(raw.get('last_role'), 255),
        'last_company': _clean(raw.get('last_company'), 255),
        'total_experience_years': _clean_decimal(raw.get('total_experience_years')),
        'skills': _clean(raw.get('skills'), 2000),
        'notice_period': _clean(raw.get('notice_period'), 100),
        'expected_salary': _clean(raw.get('expected_salary'), 100),
        'current_salary': _clean(raw.get('current_salary'), 100),
        'role_applied': _clean(raw.get('role_applied'), 255) or (role_hint or None),
        'source': _clean(raw.get('source'), 255) or (source_hint or None),
        'cv_summary': _clean(raw.get('summary')),
        'experience': _clean_experience(raw.get('experience')),
    }
