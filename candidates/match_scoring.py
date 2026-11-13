"""Direct Azure OpenAI client for Score Candidates.

Unlike CV parsing (candidates/cv_extraction.py), which needs to read the raw
file, scoring only needs text that is already in the database - the job's JD
and the candidate's parsed profile - so this calls the Azure OpenAI chat
completions REST API directly instead of going through a Logic App.

One holistic score, not a weighted rubric. This used to score skills/
experience/education/fit as separate sub-scores that summed to 100 (see git
history if you need the old shape). That produced technically-defensible but
practically wrong results: a candidate could rack up points on keyword
presence and tenure length while being a poor fit for what the role actually
needs day to day - see a real case where a ~1.3-year employee-engagement
specialist scored 94 for an HRBP role on "7 years experience" and skills that
were never on her resume, because the sub-scores rewarded surface matches
over genuine depth. The rubric wasn't wrong so much as it was answering the
wrong question ("how many boxes does this resume tick") instead of the one
HR actually needs answered ("could this person actually do this job").

So this asks for one thing: would this candidate succeed in this specific
role, based on what their profile actually demonstrates - not whether their
resume happens to contain the right words. It also has to work when a job's
`description`/`requirements` are thin or empty (common in this system) by
reasoning from the job title using ordinary expectations for that kind of
role, the same way a recruiter would size up a role they don't have a formal
JD for yet.
"""
import hashlib
import json
import logging

import requests
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from . import cv_text
from .models import ScoringCriteria
from prompts.company import COMPANY_CONTEXT
from prompts.match_scoring import (
    CONSIDER_CEILING, FACTOR_KEYS, NO_CORE_EXPERIENCE_MAX_RATING, RESPONSE_JSON_SCHEMA, SYSTEM_PROMPT,
)
from prompts.store import render_prompt

logger = logging.getLogger(__name__)

# Re-scoring an unchanged candidate against an unchanged job (HR clicking
# Re-score without anything having actually changed) would otherwise re-bill
# Azure OpenAI for an identical answer. Caching is best-effort only - a miss
# just means a normal live call - so the project's default cache (in-process
# LocMemCache; nothing extra configured in settings.py) is good enough here.
# The cache key includes ScoringCriteria's current text (see build_cache_key)
# so editing it on the Scoring Criteria page invalidates every old cached
# score on its own - no version bump needed here for that. v4 bumped the
# prompt itself (added the extra-criteria placeholder), which still needs a
# version bump since the *template*, not just the per-call inputs, changed.
# v5: added {today} (see prompts/match_scoring.py) and experience date ranges
# in _candidate_text() - both change what the model sees for the exact same
# candidate/job pair, so old cached scores need invalidating too.
CACHE_TIMEOUT = 60 * 60 * 24
# v6: full CV text in _candidate_text(), score bands + factor ratings in the
# prompt/response (prompts/match_scoring.py).
# v7: Candidate Overview / Key Strengths / Key Concerns + company context.
CACHE_PREFIX = 'match_scoring:v7:'

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
    if len(parts) == 1:
        parts.append(
            "(No job description was written for this role - judge the candidate against the ordinary, "
            "well-established expectations for a role with this title, the way an experienced recruiter "
            "would size up a role they don't have a formal JD for yet. Say so implicitly by staying "
            "appropriately conservative rather than inventing specific requirements that were never stated.)"
        )
    return '\n\n'.join(parts)


def _date_range(start, end):
    """'(Jun 2024 - Present)' style range for a Work Experience line - blank
    if neither date is known, 'Present' for a still-open end_date, so the
    model can actually tell a current job from a finished one (it previously
    got no dates here at all - see the {today} note in prompts/match_scoring.py)."""
    if not start and not end:
        return ''
    start_s = start.strftime('%b %Y') if start else '?'
    end_s = end.strftime('%b %Y') if end else 'Present'
    return f' ({start_s} - {end_s})'


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
        lines = [f'{e.designation or "-"} at {e.company_name}{_date_range(e.start_date, e.end_date)} '
                 f'- {e.skills or ""}' for e in experience]
        parts.append('Work Experience:\n' + '\n'.join(lines))

    if candidate.cv_summary:
        parts.append(f'AI CV Summary:\n{candidate.cv_summary}')

    # The whole resume, read from the CV file (see candidates/cv_text.py) -
    # the primary evidence when available. A legacy SharePoint-only CV can't
    # be read, so the model is told it's judging the parsed profile alone.
    full_cv = cv_text.get_cv_text(candidate)
    if full_cv:
        parts.append(f'FULL CV TEXT:\n{full_cv}')
    else:
        parts.append('(Full CV text not available - judge from the parsed profile above, '
                     'conservatively where it is thin.)')

    return '\n\n'.join(parts)


def _build_system_prompt(must_have, extra_criteria):
    must_have_block = '\n'.join(f'- {item}' for item in must_have) if must_have else '(none specified)'
    extra_criteria_block = ''
    if extra_criteria:
        extra_criteria_block = (
            'Additional scoring criteria set by HR - apply these on top of everything above:\n'
            f'{extra_criteria}\n\n'
        )
    today = timezone.localdate().strftime('%d %B %Y')
    return render_prompt(
        'match_scoring', SYSTEM_PROMPT,
        must_have_block=must_have_block, extra_criteria_block=extra_criteria_block, today=today,
        company_context=COMPANY_CONTEXT)


def build_cache_key(job_text, candidate_text, must_have, extra_criteria):
    payload = json.dumps(
        {'job': job_text, 'candidate': candidate_text, 'must_have': must_have, 'criteria': extra_criteria},
        sort_keys=True)
    return CACHE_PREFIX + hashlib.sha256(payload.encode('utf-8')).hexdigest()


def score_candidate(candidate, job):
    """Call Azure OpenAI and return a dict with the overall score, likes,
    not_matched gaps and a short rationale. Raises ScoreError with a message
    fit for the results screen; never raises anything else."""
    if not is_configured():
        raise ScoreError(
            'Scoring is not configured - set AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_KEY.')

    must_have = job.must_have_list
    job_text = _job_text(job)
    candidate_text = _candidate_text(candidate)
    extra_criteria = ScoringCriteria.load_for(job).extra_instructions.strip()

    cache_key = build_cache_key(job_text, candidate_text, must_have, extra_criteria)
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    system_prompt = _build_system_prompt(must_have, extra_criteria)
    user_content = f'{job_text}\n\n---\n\nCandidate Profile:\n\n{candidate_text}'
    payload = {
        'messages': [
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': user_content},
        ],
        'temperature': 0,
        'max_tokens': 2500,
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

    try:
        score = max(0, min(100, int(data.get('overall_score', 0))))
    except (TypeError, ValueError):
        score = 0

    ratings = data.get('factor_ratings') or {}
    # The model's 0-10 factor ratings (prompts/match_scoring.FACTOR_KEYS),
    # kept so HR can see what the score was built from.
    factors = {k: max(0, min(10, int(ratings[k]))) for k in FACTOR_KEYS
               if isinstance(ratings.get(k), (int, float))}
    # Safety net for the prompt's HARD RULE: no real core experience means
    # Consider at best, even if the model's overall number drifted higher.
    core = factors.get('core_function_experience')
    if core is not None and core <= NO_CORE_EXPERIENCE_MAX_RATING:
        score = min(score, CONSIDER_CEILING)
    result = {
        'score': score,
        'factors': factors,
        # Candidate Overview (shown at the top of the AI CV Summary), Key
        # Strengths (themed groups) and Key Concerns (to validate on the
        # call) - replaced likes/not_matched, which old breakdowns still carry.
        'overview': (data.get('candidate_overview') or '').strip()[:2500] or None,
        'strengths': [
            {'heading': g['heading'].strip()[:120],
             'points': [p.strip()[:300] for p in g.get('points') or [] if isinstance(p, str) and p.strip()][:6]}
            for g in (data.get('key_strengths') or [])
            if isinstance(g, dict) and isinstance(g.get('heading'), str) and g['heading'].strip()
        ][:6],
        'concerns': [
            {'title': c['title'].strip()[:120], 'detail': (c.get('detail') or '').strip()[:500]}
            for c in (data.get('key_concerns') or [])
            if isinstance(c, dict) and isinstance(c.get('title'), str) and c['title'].strip()
        ][:8],
        'rationale': (data.get('rationale') or '').strip()[:1000] or None,
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
    # match_breakdown is an unbounded TextField; this cap only guards against
    # a runaway response (the per-field caps in score_candidate keep a normal
    # one well under it).
    limit = 16000
    try:
        text = json.dumps(trimmed)
        if len(text) <= limit:
            return text
        for key in ('strengths', 'concerns', 'likes', 'not_matched'):
            if key in trimmed:
                trimmed[key] = trimmed[key][:4]
        text = json.dumps(trimmed)
        if len(text) <= limit:
            return text
        trimmed.pop('rationale', None)
        return json.dumps(trimmed)[:limit]
    except (TypeError, ValueError):
        return None
