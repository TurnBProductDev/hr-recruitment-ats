"""10 AI-generated Tele Screening call questions, based on a candidate's CV -
generated once (Candidate.screening_questions stays blank until the first
View/Generate click) and reused after that, so the same 10 questions are
still there for an interviewer to refer back to even after the candidate has
moved past Tele Screening. See candidates/views.py::CandidateScreeningQuestionsView.

Direct Azure OpenAI call, same resource as candidates/match_scoring.py -
no caching layer here, since the result is persisted on the candidate row
itself rather than recomputed.
"""
import json
import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

QUESTION_COUNT = 10

RESPONSE_JSON_SCHEMA = {
    'name': 'tele_screening_questions',
    'strict': True,
    'schema': {
        'type': 'object',
        'properties': {
            'questions': {
                'type': 'array', 'items': {'type': 'string'},
                'minItems': QUESTION_COUNT, 'maxItems': QUESTION_COUNT,
            },
        },
        'required': ['questions'],
        'additionalProperties': False,
    },
}

SYSTEM_PROMPT = (
    "You are an experienced recruiter preparing for a first-round telephonic "
    f"screening call with a job applicant. Based on the candidate's profile and "
    f"the role they applied for, write exactly {QUESTION_COUNT} screening "
    "questions to ask them on the call. Cover their background, key skills, "
    "relevant experience, career motivation, notice period/availability, and "
    "salary expectations where appropriate. Keep each question short and "
    "conversational, suited to a phone call - not a technical panel interview. "
    "Base them only on what the profile actually states - never invent specifics "
    "that aren't there."
)


class ScreeningQuestionsError(Exception):
    """Raised when questions could not be generated. Message is shown to HR."""


def is_configured():
    return bool(getattr(settings, 'AZURE_OPENAI_ENDPOINT', '') and getattr(settings, 'AZURE_OPENAI_KEY', ''))


def _endpoint_url():
    endpoint = settings.AZURE_OPENAI_ENDPOINT.rstrip('/')
    deployment = settings.AZURE_OPENAI_SCORING_DEPLOYMENT
    api_version = settings.AZURE_OPENAI_API_VERSION
    return f'{endpoint}/openai/deployments/{deployment}/chat/completions?api-version={api_version}'


def _profile_text(candidate):
    role = candidate.job.title if candidate.job else (candidate.role_applied or 'the role')
    parts = [f'Applying for: {role}']
    if candidate.qualification:
        parts.append(f'Qualification: {candidate.qualification}')
    if candidate.skills:
        parts.append(f'Self-declared skills: {candidate.skills}')
    if candidate.last_role or candidate.last_company:
        parts.append(f'Last role: {candidate.last_role or "-"} at {candidate.last_company or "-"}')
    if candidate.total_experience_years is not None:
        parts.append(f'Total experience: {candidate.total_experience_years} years')
    if candidate.cv_summary:
        parts.append(f'AI CV Summary:\n{candidate.cv_summary}')
    return '\n\n'.join(parts)


def generate_questions(candidate):
    """Call Azure OpenAI for QUESTION_COUNT tele-screening questions based on
    this candidate's profile. Raises ScreeningQuestionsError with a message
    fit for the results screen; never raises anything else. Does not save -
    the caller persists the result onto Candidate.screening_questions."""
    if not is_configured():
        raise ScreeningQuestionsError(
            'Question generation is not configured - set AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_KEY.')

    payload = {
        'messages': [
            {'role': 'system', 'content': SYSTEM_PROMPT},
            {'role': 'user', 'content': _profile_text(candidate)},
        ],
        'temperature': 0.3,
        'max_tokens': 900,
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
        raise ScreeningQuestionsError(f'Azure OpenAI did not respond within {timeout}s.')
    except requests.RequestException as exc:
        raise ScreeningQuestionsError(f'Could not reach Azure OpenAI: {exc}')

    if response.status_code >= 400:
        raise ScreeningQuestionsError(f'Azure OpenAI returned HTTP {response.status_code}: {response.text[:300]}')

    try:
        content = response.json()['choices'][0]['message']['content']
        data = json.loads(content)
        questions = [q.strip() for q in data['questions'] if isinstance(q, str) and q.strip()]
    except (ValueError, KeyError, IndexError, TypeError):
        raise ScreeningQuestionsError('Azure OpenAI returned an unexpected response shape.')

    if not questions:
        raise ScreeningQuestionsError('Azure OpenAI returned no usable questions.')
    return questions[:QUESTION_COUNT]


def dump_questions(questions):
    """JSON text for Candidate.screening_questions (never raises)."""
    try:
        return json.dumps(questions)
    except (TypeError, ValueError):
        return None


def load_questions(text):
    """Questions back out of Candidate.screening_questions (never raises)."""
    if not text:
        return []
    try:
        data = json.loads(text)
    except ValueError:
        return []
    return data if isinstance(data, list) else []
