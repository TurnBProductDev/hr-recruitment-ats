"""Direct Azure OpenAI client for Score Candidates.

Unlike CV parsing (candidates/cv_parser.py), which needs Form Recognizer OCR on
the raw file, scoring only needs text that is already in the database - the
job's JD and the candidate's parsed profile - so this calls the Azure OpenAI
chat completions REST API directly instead of going through a Logic App.
"""
import hashlib
import json
import logging

import requests
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

# Per-job weight profiles, keyed on jobs.models.Job.JobType values. Each must
# sum to 100 - the assertion below catches a typo'd new profile at import time
# rather than silently capping match_score below 100.
WEIGHT_PROFILES = {
    'technical': {'skills': 40, 'experience': 30, 'education': 15, 'fit': 15},
    'generalist': {'skills': 25, 'experience': 30, 'education': 30, 'fit': 15},
    'fresher': {'skills': 20, 'experience': 15, 'education': 40, 'fit': 25},
    'default': {'skills': 30, 'experience': 30, 'education': 30, 'fit': 10},
}
assert all(sum(profile.values()) == 100 for profile in WEIGHT_PROFILES.values()), \
    'WEIGHT_PROFILES entries must each sum to 100'

# Re-scoring an unchanged candidate against an unchanged job (HR clicking
# Re-score without anything having actually changed) would otherwise re-bill
# Azure OpenAI for an identical answer. Caching is best-effort only - a miss
# just means a normal live call - so the project's default cache (in-process
# LocMemCache; nothing extra configured in settings.py) is good enough here.
CACHE_TIMEOUT = 60 * 60 * 24
CACHE_PREFIX = 'match_scoring:v2:'

RESPONSE_JSON_SCHEMA = {
    'name': 'candidate_match_score',
    'strict': True,
    'schema': {
        'type': 'object',
        'properties': {
            'skills_score': {'type': 'integer'},
            'experience_score': {'type': 'integer'},
            'education_score': {'type': 'integer'},
            'fit_score': {'type': 'integer'},
            'matched_skills': {
                'type': 'array',
                'items': {
                    'type': 'object',
                    'properties': {
                        'skill': {'type': 'string'},
                        'evidence': {'type': 'string'},
                    },
                    'required': ['skill', 'evidence'],
                    'additionalProperties': False,
                },
            },
            'missing_skills': {'type': 'array', 'items': {'type': 'string'}},
            'disqualifiers': {'type': 'array', 'items': {'type': 'string'}},
            'rationale': {'type': 'string'},
        },
        'required': ['skills_score', 'experience_score', 'education_score', 'fit_score',
                     'matched_skills', 'missing_skills', 'disqualifiers', 'rationale'],
        'additionalProperties': False,
    },
}


class ScoreError(Exception):
    """Raised when a candidate could not be scored. Message is shown to HR."""


def is_configured():
    return bool(getattr(settings, 'AZURE_OPENAI_ENDPOINT', '') and getattr(settings, 'AZURE_OPENAI_KEY', ''))


def get_weight_profile(job_type):
    return WEIGHT_PROFILES.get(job_type, WEIGHT_PROFILES['default'])


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


def _build_system_prompt(weights, must_have):
    must_have_block = '\n'.join(f'- {item}' for item in must_have) if must_have else '(none specified)'
    return (
        "You are an expert technical recruiter scoring how well a candidate matches a "
        "job description. Score strictly from the text given - never invent skills, "
        "companies or experience that are not stated. Use this rubric out of 100, "
        "weighted for this specific role:\n"
        f"- Skills / technical fit: 0-{weights['skills']}\n"
        f"- Experience relevance and years: 0-{weights['experience']}\n"
        f"- Education / qualification: 0-{weights['education']}\n"
        f"- Overall profile fit (from the CV summary): 0-{weights['fit']}\n\n"
        "Must-have requirements for this role:\n"
        f"{must_have_block}\n\n"
        "If a must-have requirement is not clearly evidenced in the candidate profile, add a short "
        "description of it to disqualifiers. Do not lower the sub-scores solely because of a "
        "disqualifier - report it separately so a human can review it. If all must-haves are met or "
        "none were specified, return an empty disqualifiers array.\n\n"
        "For each matched skill, quote the specific phrase from the candidate profile that supports it "
        "as evidence. Never fabricate evidence - if you cannot point to text supporting a skill, do not "
        "list it as matched.\n\n"
        "Write the rationale as 1-2 sentences that justify the sub-scores using that evidence, not just "
        "a restatement of the matched/missing lists."
    )


def build_cache_key(job_text, candidate_text, weights, must_have):
    payload = json.dumps(
        {'job': job_text, 'candidate': candidate_text, 'weights': weights, 'must_have': must_have},
        sort_keys=True)
    return CACHE_PREFIX + hashlib.sha256(payload.encode('utf-8')).hexdigest()


def score_candidate(candidate, job):
    """Call Azure OpenAI and return a dict with the rubric sub-scores, total
    score, matched/missing skills, must-have disqualifiers and a rationale.
    Raises ScoreError with a message fit for the results screen; never raises
    anything else."""
    if not is_configured():
        raise ScoreError(
            'Scoring is not configured - set AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_KEY.')

    weights = get_weight_profile(job.job_type)
    must_have = job.must_have_list
    job_text = _job_text(job)
    candidate_text = _candidate_text(candidate)

    cache_key = build_cache_key(job_text, candidate_text, weights, must_have)
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    system_prompt = _build_system_prompt(weights, must_have)
    user_content = f'{job_text}\n\n---\n\nCandidate Profile:\n\n{candidate_text}'
    payload = {
        'messages': [
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': user_content},
        ],
        'temperature': 0,
        'max_tokens': 800,
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
        raise ScoreError(f'Azure OpenAI did not respond within {timeout}s.')
    except requests.RequestException as exc:
        raise ScoreError(f'Could not reach Azure OpenAI: {exc}')

    if response.status_code >= 400:
        raise ScoreError(f'Azure OpenAI returned HTTP {response.status_code}: {response.text[:300]}')

    try:
        content = response.json()['choices'][0]['message']['content']
        data = json.loads(content)
    except (ValueError, KeyError, IndexError):
        raise ScoreError('Azure OpenAI returned an unexpected response shape.')

    def clamp(key, cap):
        try:
            return max(0, min(cap, int(data.get(key, 0))))
        except (TypeError, ValueError):
            return 0

    scores = {
        'skills_score': clamp('skills_score', weights['skills']),
        'experience_score': clamp('experience_score', weights['experience']),
        'education_score': clamp('education_score', weights['education']),
        'fit_score': clamp('fit_score', weights['fit']),
    }
    total = sum(scores.values())

    matched_skills = []
    for item in (data.get('matched_skills') or [])[:20]:
        if isinstance(item, dict) and isinstance(item.get('skill'), str):
            evidence = item.get('evidence')
            matched_skills.append({
                'skill': item['skill'][:200],
                'evidence': evidence[:300] if isinstance(evidence, str) else '',
            })

    result = {
        'score': total,
        **scores,
        'matched_skills': matched_skills,
        'missing_skills': [s[:200] for s in (data.get('missing_skills') or []) if isinstance(s, str)][:20],
        'disqualifiers': [s[:300] for s in (data.get('disqualifiers') or []) if isinstance(s, str)][:20],
        'rationale': (data.get('rationale') or '').strip()[:1000] or None,
        'weights': weights,
    }

    cache.set(cache_key, result, CACHE_TIMEOUT)
    return result


def dump_breakdown(result):
    """JSON text for Candidate.match_breakdown (never raises). Fields are
    capped small enough that this fits in normal use; if it still doesn't,
    fields are dropped (not string-truncated, which would corrupt the JSON)
    until it does."""
    if result is None:
        return None
    trimmed = dict(result)
    try:
        text = json.dumps(trimmed)
        if len(text) <= 4000:
            return text
        trimmed['matched_skills'] = [m.get('skill') for m in trimmed.get('matched_skills', [])]
        text = json.dumps(trimmed)
        if len(text) <= 4000:
            return text
        trimmed.pop('rationale', None)
        return json.dumps(trimmed)[:4000]
    except (TypeError, ValueError):
        return None
