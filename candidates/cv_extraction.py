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

logger = logging.getLogger(__name__)

MIN_TEXT_CHARS = 200  # below this, treat the PDF as scanned and fall back to vision
MAX_TEXT_CHARS = 12000  # mirrors the old Logic App's own cap on CV text sent to the model
MAX_VISION_PAGES = 4  # caps cost/latency on an unusually long scanned CV

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


def _pdf_text(content):
    """The PDF's own embedded text layer - free, local, instant. Returns ''
    for a scanned/image-only PDF (no text layer to read)."""
    import fitz  # PyMuPDF - imported lazily; only needed when a CV is actually read
    try:
        with fitz.open(stream=content, filetype='pdf') as doc:
            text = '\n'.join(page.get_text() for page in doc)
    except Exception as exc:  # noqa: BLE001 - a malformed PDF must fall back, not crash the upload
        logger.warning('Could not read the PDF text layer: %s', exc)
        return ''
    return text.strip()


def _pdf_page_images(content, max_pages=MAX_VISION_PAGES):
    """Fallback for a scanned/image-only CV: render each page to a PNG so the
    vision-capable deployment can read it directly instead of a text layer."""
    import fitz
    images = []
    with fitz.open(stream=content, filetype='pdf') as doc:
        for page in doc[:max_pages]:
            pixmap = page.get_pixmap(dpi=150)
            images.append(pixmap.tobytes('png'))
    return images


RESPONSE_JSON_SCHEMA = {
    'name': 'candidate_cv_profile',
    'strict': True,
    'schema': {
        'type': 'object',
        'properties': {
            'name': {'type': ['string', 'null']},
            'email': {'type': ['string', 'null']},
            'mobile': {'type': ['string', 'null']},
            'dob': {'type': ['string', 'null'], 'description': 'YYYY-MM-DD if the CV states a birth date, else null.'},
            'current_location': {'type': ['string', 'null']},
            'linkedin': {'type': ['string', 'null']},
            'portfolio_url': {'type': ['string', 'null']},
            'qualification': {'type': ['string', 'null'],
                              'description': "Highest/most recent degree, formatted 'Degree - Institution - Year'."},
            'last_role': {'type': ['string', 'null']},
            'last_company': {'type': ['string', 'null']},
            'total_experience_years': {'type': ['number', 'null']},
            'skills': {'type': ['string', 'null'], 'description': 'Comma-separated key skills.'},
            'notice_period': {'type': ['string', 'null']},
            'expected_salary': {'type': ['string', 'null']},
            'current_salary': {'type': ['string', 'null']},
            'role_applied': {'type': ['string', 'null']},
            'source': {'type': ['string', 'null'],
                       'description': "Application source inferred from the email context (not the CV) - "
                                      "'Linked In' if from LinkedIn, 'Referral' if a referral, 'Indeed' if "
                                      "from Indeed, 'Naukri' if from Naukri, else 'Careers'. Null if there's "
                                      "no email context to infer it from."},
            'summary': {'type': ['string', 'null'],
                        'description': 'A short Markdown summary: overview, technical competencies, experience highlights, education/logistics.'},
            'experience': {
                'type': 'array',
                'items': {
                    'type': 'object',
                    'properties': {
                        'company_name': {'type': 'string'},
                        'designation': {'type': ['string', 'null']},
                        'start_date': {'type': ['string', 'null'], 'description': 'YYYY-MM if known.'},
                        'end_date': {'type': ['string', 'null'], 'description': "YYYY-MM, or null if 'Present'/current."},
                        'skills': {'type': ['string', 'null'], 'description': 'Comma-separated tools/skills used in this role.'},
                    },
                    'required': ['company_name', 'designation', 'start_date', 'end_date', 'skills'],
                    'additionalProperties': False,
                },
            },
        },
        'required': ['name', 'email', 'mobile', 'dob', 'current_location', 'linkedin', 'portfolio_url',
                     'qualification', 'last_role', 'last_company', 'total_experience_years', 'skills',
                     'notice_period', 'expected_salary', 'current_salary', 'role_applied', 'source',
                     'summary', 'experience'],
        'additionalProperties': False,
    },
}

SYSTEM_PROMPT = (
    "You are an expert HR data extraction specialist. Read the candidate's CV (given as text, or as page "
    "images if the text layer was unreadable) and extract structured fields for an ATS.\n\n"
    "CRITICAL RULES:\n"
    "1. Use only what the CV actually states - never invent, guess, or infer a value that is not clearly "
    "supported by the text. Return null for anything not stated.\n"
    "2. Email: lowercase, no internal spaces.\n"
    "3. Mobile: format as +91-XXXXXXXXXX (Indian) or +[CountryCode]-XXXXXXXXX.\n"
    "4. qualification: highest/most recent degree, formatted 'Degree - Institution - Year'.\n"
    "5. skills: a comma-separated list of the candidate's key technical/professional skills, drawn from "
    "anywhere in the CV (a Skills section, project descriptions, work experience bullets).\n"
    "6. experience: one entry per job, most recent first, with the skills/tools actually used in that role - "
    "not a repeat of the whole top-level skills list.\n"
    "7. total_experience_years: total professional experience as a decimal number (e.g. 5.5), not per-job.\n"
    "8. role_applied: the job title the candidate is applying for, from a subject line/cover note if given, "
    "else null - do not guess it from their current job title.\n"
    "9. source: only from the email context (subject/sender), never from the CV itself - 'Linked In' if it "
    "mentions LinkedIn, 'Referral' if a referral, 'Indeed' if from Indeed, 'Naukri' if from Naukri, else "
    "'Careers'. Null if there's no email context at all (e.g. a bulk upload with no email involved).\n"
    "10. summary: 4 short Markdown sections - Candidate Overview, Core Technical Competencies (a table), "
    "Professional Experience Highlights (bullets), and Education/Logistics - matching the tone of an "
    "experienced HR analyst's notes. Base it strictly on the CV text; do not invent achievements.\n\n"
    "Return ONLY the JSON object described by the schema. No markdown formatting, no commentary."
)


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

    cv_text = _pdf_text(content)[:MAX_TEXT_CHARS]
    page_images = []
    if len(cv_text) < MIN_TEXT_CHARS:
        try:
            page_images = _pdf_page_images(content)
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
