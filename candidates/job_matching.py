"""Re-checks a candidate already sitting in General Application against the
currently open vacancies - for applications that arrived before a matching
vacancy existed, or that intake's own matching missed. See
candidates/management/commands/rematch_general_applications.py.

Deliberately separate from candidates/cv_extraction.py's matched_job_title
(the same idea, run once per CV at intake time): this runs later, works off
the profile fields already on the candidate row, and never re-reads the CV.
Same Azure OpenAI resource as screening_questions.py/match_scoring.py.
"""
import json
import logging

import requests
from django.conf import settings

from prompts import job_matching as prompts
from prompts.store import render_prompt

logger = logging.getLogger(__name__)


class JobMatchError(Exception):
    """Raised when the match could not be attempted at all - the caller
    should skip this candidate and move on, not abort the whole batch."""


def is_configured():
    return bool(getattr(settings, 'AZURE_OPENAI_ENDPOINT', '') and getattr(settings, 'AZURE_OPENAI_KEY', ''))


def _endpoint_url():
    endpoint = settings.AZURE_OPENAI_ENDPOINT.rstrip('/')
    deployment = settings.AZURE_OPENAI_SCORING_DEPLOYMENT
    api_version = settings.AZURE_OPENAI_API_VERSION
    return f'{endpoint}/openai/deployments/{deployment}/chat/completions?api-version={api_version}'


def _profile_text(candidate):
    role = candidate.role_applied or 'not stated'
    parts = [f'Role applied for (as written by the applicant): {role}']
    if candidate.qualification:
        parts.append(f'Qualification: {candidate.qualification}')
    if candidate.skills:
        parts.append(f'Self-declared skills: {candidate.skills}')
    if candidate.last_role or candidate.last_company:
        parts.append(f'Last role: {candidate.last_role or "-"} at {candidate.last_company or "-"}')
    if candidate.cv_summary:
        parts.append(f'AI CV Summary:\n{candidate.cv_summary}')
    return '\n\n'.join(parts)


def match_job_title(candidate, open_job_titles):
    """Returns the matched title (one of open_job_titles, exactly) or None.
    Raises JobMatchError (never anything else) if the call itself couldn't
    be made or parsed - never invents a match on a failure."""
    if not open_job_titles:
        return None
    if not is_configured():
        raise JobMatchError(
            'Job matching is not configured - set AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_KEY.')

    system_prompt = render_prompt('job_matching', prompts.SYSTEM_PROMPT)
    user_content = (
        _profile_text(candidate) + '\n\nOpen vacancies to match against:\n' +
        '\n'.join(f'- {t}' for t in open_job_titles)
    )
    payload = {
        'messages': [
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': user_content},
        ],
        'temperature': 0,
        'max_tokens': 100,
        'response_format': {'type': 'json_schema', 'json_schema': prompts.response_json_schema(open_job_titles)},
    }
    timeout = int(getattr(settings, 'SCORE_CANDIDATES_TIMEOUT', 60))

    try:
        response = requests.post(
            _endpoint_url(),
            headers={'api-key': settings.AZURE_OPENAI_KEY, 'Content-Type': 'application/json'},
            json=payload, timeout=(10, timeout),
        )
    except requests.Timeout:
        raise JobMatchError(f'Azure OpenAI did not respond within {timeout}s.')
    except requests.RequestException as exc:
        raise JobMatchError(f'Could not reach Azure OpenAI: {exc}')

    if response.status_code >= 400:
        raise JobMatchError(f'Azure OpenAI returned HTTP {response.status_code}: {response.text[:300]}')

    try:
        content = response.json()['choices'][0]['message']['content']
        data = json.loads(content)
    except (ValueError, KeyError, IndexError, TypeError):
        raise JobMatchError('Azure OpenAI returned an unexpected response shape.')

    title = data.get('matched_job_title')
    return title if title in open_job_titles else None
