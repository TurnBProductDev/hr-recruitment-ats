"""Direct Azure OpenAI client for Score Candidates.

Unlike CV parsing (candidates/cv_parser.py), which needs Form Recognizer OCR on
the raw file, scoring only needs text that is already in the database - the
job's JD and the candidate's parsed profile - so this calls the Azure OpenAI
chat completions REST API directly instead of going through a Logic App.
"""
import json
import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

RUBRIC = (
    ('skills_score', 'Skills / technical fit', 30),
    ('experience_score', 'Experience relevance and years', 30),
    ('education_score', 'Education / qualification', 30),
    ('fit_score', 'Overall profile fit', 10),
)

SYSTEM_PROMPT = (
    "You are an expert technical recruiter scoring how well a candidate matches a "
    "job description. Score strictly from the text given - never invent skills, "
    "companies or experience that are not stated. Use this rubric out of 100:\n"
    "- Skills / technical fit: 0-30\n"
    "- Experience relevance and years: 0-30\n"
    "- Education / qualification: 0-30\n"
    "- Overall profile fit (from the CV summary): 0-10\n\n"
    "Return ONLY a raw JSON object, no markdown, with keys: "
    "skills_score (int), experience_score (int), education_score (int), fit_score (int), "
    "matched_skills (array of strings), missing_skills (array of strings), "
    "rationale (1-2 sentence explanation)."
)


class ScoreError(Exception):
    """Raised when a candidate could not be scored. Message is shown to HR."""


def is_configured():
    return bool(getattr(settings, 'AZURE_OPENAI_ENDPOINT', '') and getattr(settings, 'AZURE_OPENAI_KEY', ''))


def _endpoint_url():
    endpoint = settings.AZURE_OPENAI_ENDPOINT.rstrip('/')
    deployment = settings.AZURE_OPENAI_SCORING_DEPLOYMENT
    api_version = settings.AZURE_OPENAI_API_VERSION
    return f'{endpoint}/openai/deployments/{deployment}/chat/completions?api-version={api_version}'


def _job_text(job):
    parts = [f'Job Title: {job.title}']
    if job.description:
        parts.append(f'Description:\n{job.description}')
    if job.requirements:
        parts.append(f'Requirements:\n{job.requirements}')
    return '\n\n'.join(parts)


def _candidate_text(candidate):
    parts = [f'Qualification: {candidate.qualification or "Not stated"}']
    if candidate.last_role or candidate.last_company:
        parts.append(f'Last Role: {candidate.last_role or "-"} at {candidate.last_company or "-"}')
    if candidate.total_experience_years is not None:
        parts.append(f'Total Experience: {candidate.total_experience_years} years')
    if candidate.skills:
        parts.append(f'Key Skills (self-declared): {candidate.skills}')

    education = list(candidate.education.all())
    if education:
        lines = [f'{e.qualification} - {e.institution or "-"} ({e.year_completed or "-"})' for e in education]
        parts.append('Education History:\n' + '\n'.join(lines))

    experience = list(candidate.experience_set.all())
    if experience:
        lines = [f'{e.designation or "-"} at {e.company_name} - {e.skills or ""}' for e in experience]
        parts.append('Work Experience:\n' + '\n'.join(lines))

    if candidate.cv_summary:
        parts.append(f'AI CV Summary:\n{candidate.cv_summary}')

    return '\n\n'.join(parts)


def score_candidate(candidate, job):
    """Call Azure OpenAI and return a dict with the rubric sub-scores, total
    score, matched/missing skills and a rationale. Raises ScoreError with a
    message fit for the results screen; never raises anything else."""
    if not is_configured():
        raise ScoreError(
            'Scoring is not configured - set AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_KEY.')

    user_content = f'{_job_text(job)}\n\n---\n\nCandidate Profile:\n\n{_candidate_text(candidate)}'
    payload = {
        'messages': [
            {'role': 'system', 'content': SYSTEM_PROMPT},
            {'role': 'user', 'content': user_content},
        ],
        'temperature': 0,
        'max_tokens': 500,
    }
    timeout = int(getattr(settings, 'SCORE_CANDIDATES_TIMEOUT', 60))

    try:
        response = requests.post(
            _endpoint_url(),
            headers={'api-key': settings.AZURE_OPENAI_KEY, 'Content-Type': 'application/json'},
            json=payload, timeout=(10, timeout),
        )
    except requests.Timeout:
        raise ScoreError(f'Azure OpenAI did not respond within {timeout}s.')
    except requests.RequestException as exc:
        raise ScoreError(f'Could not reach Azure OpenAI: {exc}')

    if response.status_code >= 400:
        raise ScoreError(f'Azure OpenAI returned HTTP {response.status_code}: {response.text[:300]}')

    try:
        content = response.json()['choices'][0]['message']['content']
    except (ValueError, KeyError, IndexError):
        raise ScoreError('Azure OpenAI returned an unexpected response shape.')

    content = content.strip()
    if content.startswith('```'):
        content = content.strip('`')
        if content.lower().startswith('json'):
            content = content[4:]
        content = content.strip()

    try:
        data = json.loads(content)
    except ValueError:
        raise ScoreError(f'Azure OpenAI returned non-JSON content: {content[:300]}')

    def clamp(key, cap):
        try:
            return max(0, min(cap, int(data.get(key, 0))))
        except (TypeError, ValueError):
            return 0

    scores = {key: clamp(key, cap) for key, _, cap in RUBRIC}
    total = sum(scores.values())

    return {
        'score': total,
        **scores,
        'matched_skills': [s for s in (data.get('matched_skills') or []) if isinstance(s, str)][:20],
        'missing_skills': [s for s in (data.get('missing_skills') or []) if isinstance(s, str)][:20],
        'rationale': (data.get('rationale') or '').strip()[:1000] or None,
    }


def dump_breakdown(result):
    """JSON text for Candidate.match_breakdown (never raises)."""
    try:
        return json.dumps(result)[:4000]
    except (TypeError, ValueError):
        return None
