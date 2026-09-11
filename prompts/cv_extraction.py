"""Prompt for candidates/cv_extraction.py - reads an uploaded CV (PDF text,
or page images for a scanned CV) and extracts every Candidate field plus a
structured Experience list, in one Azure OpenAI call."""

RESPONSE_JSON_SCHEMA = {
    'name': 'candidate_cv_profile',
    'strict': True,
    'schema': {
        'type': 'object',
        'properties': {
            'name': {'type': ['string', 'null']},
            'email': {'type': ['string', 'null']},
            'mobile': {'type': ['string', 'null']},
            'dob': {'type': ['string', 'null'], 'description': 'YYYY-MM-DD if the CV states a birth date, else null.'},
            'current_location': {'type': ['string', 'null']},
            'linkedin': {'type': ['string', 'null']},
            'portfolio_url': {'type': ['string', 'null']},
            'qualification': {'type': ['string', 'null'],
                              'description': "Highest/most recent degree, formatted 'Degree - Institution - Year'."},
            'last_role': {'type': ['string', 'null']},
            'last_company': {'type': ['string', 'null']},
            'total_experience_years': {'type': ['number', 'null']},
            'skills': {'type': ['string', 'null'], 'description': 'Comma-separated key skills.'},
            'notice_period': {'type': ['string', 'null']},
            'expected_salary': {'type': ['string', 'null']},
            'current_salary': {'type': ['string', 'null']},
            'role_applied': {'type': ['string', 'null']},
            'source': {'type': ['string', 'null'],
                       'description': "Application source inferred from the email context (not the CV) - "
                                      "'Linked In' if from LinkedIn, 'Referral' if a referral, 'Indeed' if "
                                      "from Indeed, 'Naukri' if from Naukri, else 'Careers'. Null if there's "
                                      "no email context to infer it from."},
            'summary': {'type': ['string', 'null'],
                        'description': 'A short Markdown summary: overview, technical competencies, experience highlights, education/logistics.'},
            'experience': {
                'type': 'array',
                'items': {
                    'type': 'object',
                    'properties': {
                        'company_name': {'type': 'string'},
                        'designation': {'type': ['string', 'null']},
                        'start_date': {'type': ['string', 'null'], 'description': 'YYYY-MM if known.'},
                        'end_date': {'type': ['string', 'null'], 'description': "YYYY-MM, or null if 'Present'/current."},
                        'skills': {'type': ['string', 'null'], 'description': 'Comma-separated tools/skills used in this role.'},
                    },
                    'required': ['company_name', 'designation', 'start_date', 'end_date', 'skills'],
                    'additionalProperties': False,
                },
            },
        },
        'required': ['name', 'email', 'mobile', 'dob', 'current_location', 'linkedin', 'portfolio_url',
                     'qualification', 'last_role', 'last_company', 'total_experience_years', 'skills',
                     'notice_period', 'expected_salary', 'current_salary', 'role_applied', 'source',
                     'summary', 'experience'],
        'additionalProperties': False,
    },
}

SYSTEM_PROMPT = (
    "You are an expert HR data extraction specialist. Read the candidate's CV (given as text, or as page "
    "images if the text layer was unreadable) and extract structured fields for an ATS.\n\n"
    "CRITICAL RULES:\n"
    "1. Use only what the CV actually states - never invent, guess, or infer a value that is not clearly "
    "supported by the text. Return null for anything not stated.\n"
    "2. Email: lowercase, no internal spaces.\n"
    "3. Mobile: format as +91-XXXXXXXXXX (Indian) or +[CountryCode]-XXXXXXXXX.\n"
    "4. qualification: highest/most recent degree, formatted 'Degree - Institution - Year'.\n"
    "5. skills: a comma-separated list of the candidate's key technical/professional skills, drawn from "
    "anywhere in the CV (a Skills section, project descriptions, work experience bullets).\n"
    "6. experience: one entry per job, most recent first, with the skills/tools actually used in that role - "
    "not a repeat of the whole top-level skills list.\n"
    "7. total_experience_years: total professional experience as a decimal number (e.g. 5.5), not per-job.\n"
    "8. role_applied: the job title the candidate is applying for, from a subject line/cover note if given, "
    "else null - do not guess it from their current job title.\n"
    "9. source: only from the email context (subject/sender), never from the CV itself - 'Linked In' if it "
    "mentions LinkedIn, 'Referral' if a referral, 'Indeed' if from Indeed, 'Naukri' if from Naukri, else "
    "'Careers'. Null if there's no email context at all (e.g. a bulk upload with no email involved).\n"
    "10. summary: 4 short Markdown sections - Candidate Overview, Core Technical Competencies (a table), "
    "Professional Experience Highlights (bullets), and Education/Logistics - matching the tone of an "
    "experienced HR analyst's notes. Base it strictly on the CV text; do not invent achievements.\n\n"
    "Return ONLY the JSON object described by the schema. No markdown formatting, no commentary."
)
