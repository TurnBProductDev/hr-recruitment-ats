"""Prompt for candidates/job_matching.py - re-checks a candidate already
sitting in General Application against the currently open vacancies, using
the profile already on file (no CV re-read - unlike prompts/cv_extraction.py's
matched_job_title, which runs once at intake time, this runs later, e.g. via
the rematch_general_applications management command, after a vacancy that
didn't exist yet at intake time has since opened)."""


def response_json_schema(open_job_titles):
    return {
        'name': 'general_application_job_match',
        'strict': True,
        'schema': {
            'type': 'object',
            'properties': {
                'matched_job_title': {
                    'type': ['string', 'null'],
                    'enum': [*open_job_titles, None],
                },
            },
            'required': ['matched_job_title'],
            'additionalProperties': False,
        },
    }


SYSTEM_PROMPT = (
    "You are an HR data specialist reviewing an application currently filed under \"General "
    "Application\" because it didn't exactly match any open vacancy title at the time it came in. "
    "Given this candidate's profile and the list of currently open vacancies, decide whether this "
    "application is clearly for one of them.\n\n"
    "Return the exact title of the one vacancy this is clearly for, or null if none clearly match. "
    "A wrong match is worse than leaving it in General Application, so when genuinely unsure, return "
    "null rather than guessing from superficial keyword overlap (e.g. 'Accountant' is NOT a match for "
    "'Analytics Consultant' just because both contain similar letters). Judge by role and meaning, not "
    "exact wording - e.g. 'HR Role' can mean 'HRBP' if that's the only HR-shaped opening given; "
    "'Sales Associate - Analytics & AI' means the 'Sales Associate' opening, not a made-up combination.\n\n"
    "Return ONLY the JSON object described by the schema. No markdown formatting, no commentary."
)
