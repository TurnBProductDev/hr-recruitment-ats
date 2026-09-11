"""Every AI prompt (system prompt + response JSON schema) used anywhere in
the app, collected in one place so the wording that steers Azure OpenAI can
be found and edited without hunting through the modules that call it.

One file per use case, named after the module that calls it:
- cv_extraction.py       -> candidates/cv_extraction.py    (reads an uploaded CV)
- match_scoring.py       -> candidates/match_scoring.py    (Score Candidates)
- profile_extraction.py  -> candidates/profile_extraction.py (backfill from the CV summary)
- screening_questions.py -> candidates/screening_questions.py (Tele Screening questions)
- jd_extraction.py       -> jobs/jd_extraction.py           (reads an uploaded JD file)

Each file exports SYSTEM_PROMPT and RESPONSE_JSON_SCHEMA (as plain values, or
as a small function when the schema/wording depends on a value the caller
owns, e.g. how many questions to generate) - nothing else lives here. The
calling module still owns everything about *how* the call is made (the HTTP
request, timeouts, caching, error handling, response parsing) - only the
prompt text and response contract moved.
"""
