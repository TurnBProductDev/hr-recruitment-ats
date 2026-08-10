"""Client for the `cv-parse-single` Azure Logic App.

Bulk-uploaded CVs go through the *same* Form Recognizer + Azure OpenAI
extraction as the careers-mailbox intake flow, by POSTing one CV at a time to an
HTTP-triggered Logic App and mapping its JSON response onto Candidate fields.

See logic_apps/README.md for the workflow and its request/response contract.
"""
import base64
import json
import logging
import re
import time

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

# "Degree - College Name - Year" is the format the extraction prompt asks for.
_YEAR_RE = re.compile(r'^[12][0-9]{3}$')
_EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')


class CVParseError(Exception):
    """Raised when a CV could not be parsed. The message is shown to HR."""


def is_configured():
    return bool(getattr(settings, 'LOGIC_APP_CV_PARSER_URL', ''))


def parse_cv(filename, content, role_hint=None, source_hint=None):
    """POST one CV to the Logic App and return its parsed JSON.

    `content` is the raw file bytes. Raises CVParseError with a message fit for
    the results screen; never raises anything else.
    """
    if not is_configured():
        raise CVParseError(
            'CV parsing is not configured - set LOGIC_APP_CV_PARSER_URL '
            '(see logic_apps/README.md).')

    payload = {
        'filename': filename,
        'content_base64': base64.b64encode(content).decode('ascii'),
        'role_hint': role_hint or '',
        'source_hint': source_hint or '',
        'upload_to_sharepoint': bool(getattr(settings, 'CV_PARSER_UPLOAD_TO_SHAREPOINT', True)),
    }
    timeout = int(getattr(settings, 'CV_PARSER_TIMEOUT', 180))
    deadline = time.monotonic() + timeout

    try:
        response = requests.post(
            settings.LOGIC_APP_CV_PARSER_URL, json=payload,
            timeout=(15, min(timeout, 120)),
        )
        # A CV that outruns the trigger's synchronous window comes back as
        # 202 + Location; poll that URL until the run finishes.
        while response.status_code == 202:
            location = response.headers.get('Location')
            if not location:
                raise CVParseError('Logic App accepted the CV but returned no status URL.')
            if time.monotonic() >= deadline:
                raise CVParseError(f'Timed out after {timeout}s waiting for the Logic App.')
            time.sleep(5)
            response = requests.get(location, timeout=(15, 60))
    except requests.Timeout:
        raise CVParseError(f'The Logic App did not respond within {timeout}s.')
    except requests.RequestException as exc:
        raise CVParseError(f'Could not reach the Logic App: {exc}')

    if response.status_code >= 400:
        raise CVParseError(
            f'Logic App returned HTTP {response.status_code}: {response.text[:300]}')

    try:
        data = response.json()
    except ValueError:
        raise CVParseError(f'Logic App returned a non-JSON response: {response.text[:300]}')

    if not isinstance(data, dict):
        raise CVParseError('Logic App returned an unexpected response shape.')

    if str(data.get('status', '')).lower() == 'error':
        action = data.get('action')
        message = data.get('message') or 'CV parsing failed.'
        raise CVParseError(f'{message} (failed step: {action})' if action else message)

    return data


def split_education(text):
    """Split "Degree - College Name - Year" into (qualification, institution,
    year). Mirrors the parse inside sql/sp_intake_add_candidate.sql so bulk rows
    and intake rows produce identical CandidateEducation records."""
    edu = (text or '').strip()
    if not edu:
        return None, None, None

    qualification, institution, year = edu, '', None
    pos = edu.find(' - ')
    if pos >= 0:
        qualification = edu[:pos].strip()
        institution = edu[pos + 3:].strip()
        # strip a trailing " - YYYY" into the year
        if len(institution) >= 7 and institution[-7:-4] == ' - ' and _YEAR_RE.match(institution[-4:]):
            year = int(institution[-4:])
            institution = institution[:-7].strip()

    return (qualification or 'N/A')[:255], institution[:255] or None, year


def map_to_candidate_fields(data, fallback_name=''):
    """Turn the Logic App response into Candidate field values.

    Returns (fields, warning). `warning` collects everything HR should look at on
    a CV that parsed but came back incomplete - the candidate is still created
    and flagged on the results screen. `fallback_name` (normally guessed from the
    filename) is used when the CV has no readable name.
    """
    def clean(key, limit=None):
        value = (data.get(key) or '').strip()
        # The Logic App fills unresolved expressions with an empty string, but a
        # model that found nothing sometimes echoes "null"/"N/A" instead.
        if value.lower() in ('null', 'none', 'n/a', 'not found', 'not mentioned'):
            return ''
        return value[:limit] if limit else value

    warnings = []
    email = clean('Email').lower().replace(' ', '')
    if not _EMAIL_RE.match(email):
        email = ''
        warnings.append('No usable email address found in the CV - please add one.')

    fields = {
        'full_name': clean('Name', 255),
        'email': email,
        'phone': clean('Mobile', 20) or None,
        'qualification': clean('Education', 255) or None,
        'cv_summary': clean('Summary') or None,
        'resume_url': clean('CV_Link', 1000) or None,
    }
    if not fields['full_name']:
        fields['full_name'] = (fallback_name or 'Unnamed Candidate')[:255]
        warnings.append('No name found in the CV - guessed from the file name.')
    if not fields['cv_summary']:
        warnings.append('No AI summary was produced.')
    # The workflow answers with an empty CV_Link when the SharePoint upload
    # failed; the candidate is still usable, but nobody can open the CV from
    # the link, so say so rather than leaving it silently blank.
    if getattr(settings, 'CV_PARSER_UPLOAD_TO_SHAREPOINT', True) and not fields['resume_url']:
        warnings.append('CV was not filed to SharePoint - no CV link.')

    return fields, ' · '.join(warnings)[:255] or None


def dump_response(data):
    """JSON text for BulkUploadItem.parsed_json (never raises)."""
    try:
        return json.dumps(data)[:8000]
    except (TypeError, ValueError):
        return None
