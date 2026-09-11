"""Prompt for candidates/profile_extraction.py - lifts qualification/
last_role/last_company/total_experience_years/skills back out of the AI CV
Summary when an intake pipeline left them blank.

response_json_schema() takes candidates.profile_extraction.TARGET_FIELDS so
the schema's required-field list can never drift out of sync with the tuple
that actually drives what gets backfilled. SYSTEM_PROMPT itself is a fixed
prompt (it doesn't need TARGET_FIELDS - it already names the 5 fields
directly), so it stays a plain constant.
"""

SYSTEM_PROMPT = (
    "You extract structured candidate fields from an AI-generated CV summary for "
    "an ATS. Use only what the summary states - never invent or guess a value. "
    "Return:\n"
    "- qualification: their highest or most recent educational qualification "
    "(degree + field), e.g. 'MBA - Finance'.\n"
    "- last_role: their most recent job title.\n"
    "- last_company: their most recent employer.\n"
    "- total_experience_years: total professional experience in years, as a "
    "number (e.g. 5.5). Convert phrasing like 'X years Y months' to a decimal.\n"
    "- skills: a comma-separated list of their key technical/professional skills.\n"
    "Return null for any field the summary does not clearly support."
)


def response_json_schema(target_fields):
    return {
        'name': 'candidate_profile_fields',
        'strict': True,
        'schema': {
            'type': 'object',
            'properties': {
                'qualification': {'type': ['string', 'null']},
                'last_role': {'type': ['string', 'null']},
                'last_company': {'type': ['string', 'null']},
                'total_experience_years': {'type': ['number', 'null']},
                'skills': {'type': ['string', 'null']},
            },
            'required': list(target_fields),
            'additionalProperties': False,
        },
    }
