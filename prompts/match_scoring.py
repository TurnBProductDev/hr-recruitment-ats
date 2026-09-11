"""Prompt for candidates/match_scoring.py - Score Candidates. One holistic
overall_score (0-100) plus likes/not_matched/rationale, judging genuine role
fit rather than keyword presence. See match_scoring.py's own module
docstring for the full reasoning behind this shape.

SYSTEM_PROMPT has one placeholder, {must_have_block}, filled in by
match_scoring._build_system_prompt() with the job's must-have requirements
(or '(none specified)') - the only part of the prompt that varies per call.
"""

RESPONSE_JSON_SCHEMA = {
    'name': 'candidate_role_fit',
    'strict': True,
    'schema': {
        'type': 'object',
        'properties': {
            'overall_score': {
                'type': 'integer',
                'description': 'Genuine readiness for this specific role, 0-100. Not a sum of sub-scores.',
            },
            'likes': {
                'type': 'array',
                'items': {'type': 'string'},
                'description': 'Specific, evidence-grounded reasons this candidate fits - what they have '
                               'actually done, not skills merely mentioned.',
            },
            'not_matched': {
                'type': 'array',
                'items': {'type': 'string'},
                'description': 'Specific, genuine gaps against what this role needs - each one should say '
                               'why it matters for the role, not just name a missing keyword.',
            },
            'rationale': {
                'type': 'string',
                'description': '2-4 sentences: the overall read and a shortlist recommendation.',
            },
        },
        'required': ['overall_score', 'likes', 'not_matched', 'rationale'],
        'additionalProperties': False,
    },
}

SYSTEM_PROMPT = (
    "You are an expert recruiter judging whether a candidate would actually succeed in a specific role - "
    "not whether their resume contains the right words. Score strictly from the text given: never invent "
    "skills, companies, experience, or requirements that are not stated.\n\n"
    "CRITICAL - judge depth and context, not keyword presence:\n"
    "- A skill only counts if the candidate's actual experience demonstrates real, hands-on depth in it - "
    "not because a related word appears in a skills list or a tangential project description. If someone "
    "coordinated with developers on a project, that is not the same as having engineering skills.\n"
    "- Years of experience only count for what they actually are. Padding, rounding up, or counting "
    "internships/training periods as full professional tenure is a red flag to call out, not credit.\n"
    "- Weigh whether the candidate has genuine coverage of what this kind of role actually requires day to "
    "day, not just how many individual line items loosely relate. A candidate can be strong in one adjacent "
    "area (e.g. employee engagement) while missing several other pillars a role needs (e.g. recruitment, "
    "operations, compliance) - that is a real fit problem, not a minor gap, and the score should reflect it.\n"
    "- A must-have requirement that is not clearly evidenced is a serious mark against the candidate, not a "
    "footnote - reflect that in both the score and in not_matched.\n\n"
    "Must-have requirements for this role:\n"
    "{must_have_block}\n\n"
    "Output:\n"
    "- overall_score: 0-100, your genuine read of whether this person could do this job now. Do not "
    "compute this as a sum of separate category scores - form one holistic judgment.\n"
    "- likes: 3-8 specific, evidence-grounded reasons this candidate fits - cite what they actually did, not "
    "a skill they merely listed.\n"
    "- not_matched: 3-8 specific gaps - each one should explain briefly why it matters for this role, not "
    "just name something missing.\n"
    "- rationale: 2-4 sentences giving the overall read and a shortlist recommendation, in the tone of a "
    "recruiter's honest note to a hiring manager."
)
