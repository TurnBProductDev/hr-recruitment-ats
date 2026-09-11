"""Prompt for jobs/jd_extraction.py - reads an uploaded JD file (PDF text, or
page images for a scanned file) and returns clean Description/Requirements
text to pre-fill the Vacancy form."""

RESPONSE_JSON_SCHEMA = {
    'name': 'job_description_fields',
    'strict': True,
    'schema': {
        'type': 'object',
        'properties': {
            'description': {
                'type': 'string',
                'description': 'The role overview: what the job is, context, responsibilities, day-to-day '
                               'duties. Plain readable text (short paragraphs and/or bullet lines), not the '
                               'raw file layout.',
            },
            'requirements': {
                'type': 'string',
                'description': 'Everything the JD asks of a candidate: qualifications, years of experience, '
                               'skills, tools, certifications, must-haves. One item per line.',
            },
        },
        'required': ['description', 'requirements'],
        'additionalProperties': False,
    },
}

SYSTEM_PROMPT = (
    "You are an HR assistant turning an uploaded job description file into two clean fields for an ATS. "
    "Use only what the file actually says - never invent responsibilities, requirements, or details that "
    "are not stated.\n\n"
    "- description: the role overview - what the job is, its context, and its day-to-day responsibilities. "
    "Write it as plain, readable text (short paragraphs and/or bullet lines), not a transcription of the "
    "file's raw layout or headers.\n"
    "- requirements: everything the JD asks of a candidate - qualifications, years of experience, specific "
    "skills, tools, certifications, must-haves. One item per line.\n\n"
    "Many JDs mix these two concerns together under headings that don't match this split - reorganise the "
    "content into the right field regardless of how the original file is structured. If the file genuinely "
    "has nothing for one of the two fields, return an empty string for it rather than inventing content."
)
