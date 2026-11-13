"""AI-generated Tele Screening call guide for a candidate - 4 themed sections
of tailored questions, a practical role-play and a 5-question "must ask"
shortlist (see prompts/screening_questions.py), built from the full CV, the
vacancy's JD and the CV Score's Key Concerns. Generated once
(Candidate.screening_questions stays blank until the first View/Generate
click) and reused after that, so it's still there to refer back to after the
candidate moves past Tele Screening; HR can Regenerate it. See
candidates/views.py::CandidateScreeningQuestionsView.

Direct Azure OpenAI call, same resource as candidates/match_scoring.py -
no caching layer here, since the result is persisted on the candidate row
itself rather than recomputed.
"""
import json
import logging

import requests
from django.conf import settings

from . import cv_text

from prompts import screening_questions as prompts
from prompts.company import COMPANY_CONTEXT
from prompts.store import render_prompt

logger = logging.getLogger(__name__)

# Approximate total across the 4 sections (the must-ask 5 and the role-play
# come on top) - see prompts/screening_questions.py.
QUESTION_COUNT = 20

RESPONSE_JSON_SCHEMA = prompts.response_json_schema(QUESTION_COUNT)
# A template default (the {question_count} token left literal, not baked in)
# so an admin-edited override (prompts/store.py) can still have the real
# count substituted in at call time - see generate_questions() below.
_DEFAULT_SYSTEM_PROMPT_TEMPLATE = prompts.system_prompt('{question_count}')


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
    """Everything distinctive about this candidate, so the model has enough
    to write specific questions instead of falling back to generic ones.
    Pulls the full CandidateExperience/CandidateEducation history (present
    for CVs read by candidates/cv_extraction.py) rather than just the
    single most-recent qualification/last_role snapshot on the candidate
    row - two candidates can share the same self-declared skills line and
    current role, but their full work history rarely matches, and that's
    the material that makes a question specific rather than boilerplate."""
    role = candidate.job.title if candidate.job else (candidate.role_applied or 'the role')
    parts = [f'Applying for: {role}']

    education = list(candidate.education.all())
    if education:
        lines = []
        for edu in education:
            bit = edu.qualification
            if edu.specialization:
                bit += f' ({edu.specialization})'
            if edu.institution:
                bit += f' - {edu.institution}'
            if edu.year_completed:
                bit += f', {edu.year_completed}'
            lines.append(f'- {bit}')
        parts.append('Education:\n' + '\n'.join(lines))
    elif candidate.qualification:
        parts.append(f'Qualification: {candidate.qualification}')

    if candidate.skills:
        parts.append(f'Self-declared skills: {candidate.skills}')

    experience = list(candidate.experience_set.all())
    if experience:
        lines = []
        for exp in experience:
            bit = f'{exp.designation or "Role"} at {exp.company_name}'
            if exp.start_date or exp.end_date:
                bit += f' ({exp.start_date or "?"} to {exp.end_date or "present"})'
            if exp.skills:
                bit += f' - worked with: {exp.skills}'
            lines.append(f'- {bit}')
        parts.append('Work history (most recent first):\n' + '\n'.join(lines))
    elif candidate.last_role or candidate.last_company:
        parts.append(f'Last role: {candidate.last_role or "-"} at {candidate.last_company or "-"}')

    if candidate.total_experience_years is not None:
        parts.append(f'Total experience: {candidate.total_experience_years} years')
    if candidate.cv_summary:
        parts.append(f'AI CV Summary:\n{candidate.cv_summary}')

    job = candidate.job
    if job and (job.description or job.requirements):
        jd = '\n\n'.join(t for t in (job.description, job.requirements) if t)
        parts.append(f'Vacancy job description:\n{jd}')

    # Concerns flagged when the CV was scored (candidates/match_scoring.py) -
    # the guide should validate each of these on the call.
    concerns = (candidate.match_breakdown_parsed or {}).get('concerns') or []
    if concerns:
        lines = [f"- {c.get('title')}: {c.get('detail')}" for c in concerns if c.get('title')]
        parts.append('Key concerns flagged at CV scoring (validate these):\n' + '\n'.join(lines))

    full_cv = cv_text.get_cv_text(candidate)
    if full_cv:
        parts.append(f'FULL CV TEXT:\n{full_cv}')
    return '\n\n'.join(parts)


def generate_questions(candidate):
    """Call Azure OpenAI for this candidate's Tele Screening guide. Returns
    {'must_ask': [...], 'sections': [{'title', 'questions'}], 'role_play':
    {...} or None}. Raises ScreeningQuestionsError with a message fit for the
    results screen; never raises anything else. Does not save - the caller
    persists the result onto Candidate.screening_questions (dump_questions)."""
    if not is_configured():
        raise ScreeningQuestionsError(
            'Question generation is not configured - set AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_KEY.')

    system_prompt = render_prompt(
        'screening_questions', _DEFAULT_SYSTEM_PROMPT_TEMPLATE,
        question_count=QUESTION_COUNT, company_context=COMPANY_CONTEXT)
    payload = {
        'messages': [
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': _profile_text(candidate)},
        ],
        'temperature': 0.3,
        'max_tokens': 3000,
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
        guide = _normalise(json.loads(content))
    except (ValueError, KeyError, IndexError, TypeError):
        raise ScreeningQuestionsError('Azure OpenAI returned an unexpected response shape.')

    if not guide['sections']:
        raise ScreeningQuestionsError('Azure OpenAI returned no usable questions.')
    return guide


def _strings(items, limit, cap=400):
    return [s.strip()[:cap] for s in (items or []) if isinstance(s, str) and s.strip()][:limit]


def _normalise(data):
    """The guide in its stored/displayed shape, from the model's JSON or a
    stored copy - anything malformed just drops out. A stored plain list (the
    old 10-question format) becomes one untitled section, so questions
    generated before this change still display."""
    if isinstance(data, list):
        questions = _strings(data, 30)
        return {'must_ask': [], 'sections': [{'title': '', 'questions': questions}] if questions else [],
                'role_play': None}
    if not isinstance(data, dict):
        raise TypeError('guide must be a dict or list')
    sections = []
    for sec in data.get('sections') or []:
        if isinstance(sec, dict):
            questions = _strings(sec.get('questions'), 10)
            if questions:
                sections.append({'title': str(sec.get('title') or '').strip()[:120], 'questions': questions})
    role_play = None
    rp = data.get('role_play')
    if isinstance(rp, dict) and isinstance(rp.get('scenario'), str) and rp['scenario'].strip():
        role_play = {'scenario': rp['scenario'].strip()[:600],
                     'client_challenges': _strings(rp.get('client_challenges'), 6),
                     'ask_candidate_to': _strings(rp.get('ask_candidate_to'), 6)}
    return {'must_ask': _strings(data.get('must_ask'), 5), 'sections': sections[:6], 'role_play': role_play}


def dump_questions(guide):
    """JSON text for Candidate.screening_questions (never raises)."""
    try:
        return json.dumps(guide)
    except (TypeError, ValueError):
        return None


def load_questions(text):
    """The stored guide, normalised (see _normalise), or None if there's
    nothing usable stored. Never raises."""
    if not text:
        return None
    try:
        guide = _normalise(json.loads(text))
    except (ValueError, TypeError):
        return None
    return guide if guide['sections'] else None
