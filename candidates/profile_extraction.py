"""Best-effort backfill for structured Candidate fields the two CV intake
pipelines sometimes leave blank.

Both the careers-mailbox intake (sp_intake_add_candidate, via the
CV-Automation-Flow Logic App) and Bulk Upload CV (candidates/cv_parser.py, via
the cv-parse-single Logic App) only extract what the shared Form Recognizer
model (MyCVModel) is labelled for: Name, Email, Mobile, Education - see
logic_apps/README.md's "Extending the extracted fields" section. qualification,
last_role, last_company, total_experience_years and skills are otherwise left
for HR to type in manually.

The AI CV Summary (Candidate.cv_summary) is generated separately from the full
CV text and reliably describes all of these in prose/Markdown, so this module
asks Azure OpenAI to lift the missing fields back out of that summary instead
of retraining Form Recognizer. It never overwrites a field that already has a
value - whatever an intake pipeline or HR already captured wins.
"""
import hashlib
import json
import logging

import requests
from django.conf import settings
from django.core.cache import cache

from prompts import profile_extraction as prompts

logger = logging.getLogger(__name__)

# Cached per summary text so re-running this on an unchanged candidate (e.g. the
# backfill command run twice, or scoring retried) doesn't re-bill Azure OpenAI.
CACHE_TIMEOUT = 60 * 60 * 24
CACHE_PREFIX = 'profile_extraction:v1:'

TARGET_FIELDS = ('qualification', 'last_role', 'last_company', 'total_experience_years', 'skills')

RESPONSE_JSON_SCHEMA = prompts.response_json_schema(TARGET_FIELDS)
SYSTEM_PROMPT = prompts.SYSTEM_PROMPT


class ExtractionError(Exception):
    """Raised when the summary could not be parsed into fields."""


def is_configured():
    return bool(getattr(settings, 'AZURE_OPENAI_ENDPOINT', '') and getattr(settings, 'AZURE_OPENAI_KEY', ''))


def _endpoint_url():
    endpoint = settings.AZURE_OPENAI_ENDPOINT.rstrip('/')
    deployment = settings.AZURE_OPENAI_SCORING_DEPLOYMENT
    api_version = settings.AZURE_OPENAI_API_VERSION
    return f'{endpoint}/openai/deployments/{deployment}/chat/completions?api-version={api_version}'


def missing_fields(candidate):
    """Which of TARGET_FIELDS are still blank on this candidate."""
    return [f for f in TARGET_FIELDS if not getattr(candidate, f)]


def _cache_key(cv_summary):
    return CACHE_PREFIX + hashlib.sha256(cv_summary.encode('utf-8')).hexdigest()


def extract_from_summary(cv_summary):
    """Call Azure OpenAI to pull TARGET_FIELDS out of an AI CV Summary.

    Raises ExtractionError with a loggable message; never raises anything else.
    """
    if not is_configured():
        raise ExtractionError(
            'Profile extraction is not configured - set AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_KEY.')

    cache_key = _cache_key(cv_summary)
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    payload = {
        'messages': [
            {'role': 'system', 'content': SYSTEM_PROMPT},
            {'role': 'user', 'content': f'AI CV Summary:\n\n{cv_summary}'},
        ],
        'temperature': 0,
        'max_tokens': 400,
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
        raise ExtractionError(f'Azure OpenAI did not respond within {timeout}s.')
    except requests.RequestException as exc:
        raise ExtractionError(f'Could not reach Azure OpenAI: {exc}')

    if response.status_code >= 400:
        raise ExtractionError(f'Azure OpenAI returned HTTP {response.status_code}: {response.text[:300]}')

    try:
        content = response.json()['choices'][0]['message']['content']
        data = json.loads(content)
    except (ValueError, KeyError, IndexError):
        raise ExtractionError('Azure OpenAI returned an unexpected response shape.')

    def clean_str(key, limit):
        value = data.get(key)
        return value.strip()[:limit] if isinstance(value, str) and value.strip() else None

    exp = data.get('total_experience_years')
    try:
        exp = round(float(exp), 1) if exp is not None else None
    except (TypeError, ValueError):
        exp = None

    result = {
        'qualification': clean_str('qualification', 255),
        'last_role': clean_str('last_role', 255),
        'last_company': clean_str('last_company', 255),
        'total_experience_years': exp,
        'skills': clean_str('skills', 2000),
    }

    cache.set(cache_key, result, CACHE_TIMEOUT)
    return result


def apply_missing_fields(candidate, save=True):
    """Fill whichever of TARGET_FIELDS are blank on `candidate` from their AI
    CV Summary. Never overwrites a field that already has a value. Never
    raises - a failed or skipped extraction just leaves the blanks as they
    were, so this is always safe to call from a background worker.

    Returns the list of fields actually filled.
    """
    missing = missing_fields(candidate)
    if not missing or not candidate.cv_summary:
        return []

    try:
        extracted = extract_from_summary(candidate.cv_summary)
    except ExtractionError as exc:
        logger.warning('Profile extraction skipped for candidate %s: %s', candidate.pk, exc)
        return []
    except Exception:  # noqa: BLE001 - a bad extraction must not break the caller
        logger.exception('Profile extraction failed for candidate %s', candidate.pk)
        return []

    filled = []
    for field in missing:
        value = extracted.get(field)
        if value not in (None, ''):
            setattr(candidate, field, value)
            filled.append(field)

    if filled and save:
        candidate.save(update_fields=filled + ['updated_at'])
    return filled
