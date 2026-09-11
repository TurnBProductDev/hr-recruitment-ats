"""Prompt for candidates/screening_questions.py - the 10 Tele Screening call
questions generated from a candidate's profile.

Both the schema and the wording depend on how many questions to generate
(candidates/screening_questions.py's QUESTION_COUNT), so this exposes
functions rather than plain constants - the caller passes its own count in,
keeping that number defined in exactly one place.
"""


def response_json_schema(question_count):
    return {
        'name': 'tele_screening_questions',
        'strict': True,
        'schema': {
            'type': 'object',
            'properties': {
                'questions': {
                    'type': 'array', 'items': {'type': 'string'},
                    'minItems': question_count, 'maxItems': question_count,
                },
            },
            'required': ['questions'],
            'additionalProperties': False,
        },
    }


def system_prompt(question_count):
    return (
        "You are an experienced recruiter preparing for a first-round telephonic "
        f"screening call with a job applicant. Based on the candidate's profile and "
        f"the role they applied for, write exactly {question_count} screening "
        "questions to ask them on the call. Cover their background, key skills, "
        "relevant experience, career motivation, notice period/availability, and "
        "salary expectations where appropriate. Keep each question short and "
        "conversational, suited to a phone call - not a technical panel interview. "
        "Base them only on what the profile actually states - never invent specifics "
        "that aren't there."
    )
