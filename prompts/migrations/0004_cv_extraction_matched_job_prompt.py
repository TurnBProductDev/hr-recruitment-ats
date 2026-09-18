from django.db import migrations

from prompts.cv_extraction import SYSTEM_PROMPT as NEW_DEFAULT

# The exact text actually live in production's seeded row (confirmed by
# reading it directly - see below), NOT what 0002_seed_defaults.py's own
# source function returns today. Some earlier commit extended SYSTEM_PROMPT
# (added rule 6b, "education") without a matching data migration, so
# production's row silently drifted out of sync with the code and has been
# missing that rule ever since 0002 first seeded it on 2026-09-15. Comparing
# in Python, not SQL - filtering on text=OLD_DEFAULT crashes SQL Server on
# this backend for a long enough string ("the data types nvarchar(max) and
# ntext are incompatible in the equal to operator", ODBC Driver 18).
OLD_DEFAULT = (
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


def update_prompt(apps, schema_editor):
    PromptTemplate = apps.get_model('prompts', 'PromptTemplate')
    # Compare in Python, not SQL - filtering on text=OLD_DEFAULT crashes SQL
    # Server on this backend ("the data types nvarchar(max) and ntext are
    # incompatible in the equal to operator", ODBC Driver 18): a long bound
    # string parameter apparently gets sent as ntext, which SQL Server
    # refuses to compare against an nvarchar(max) column with '='. Only
    # overwrite a row still at the old wording - an admin who already
    # customised this prompt keeps their own text untouched.
    row = PromptTemplate.objects.filter(key='cv_extraction').first()
    if row and row.text == OLD_DEFAULT:
        row.text = NEW_DEFAULT
        row.save(update_fields=['text'])


def revert_prompt(apps, schema_editor):
    PromptTemplate = apps.get_model('prompts', 'PromptTemplate')
    row = PromptTemplate.objects.filter(key='cv_extraction').first()
    if row and row.text == NEW_DEFAULT:
        row.text = OLD_DEFAULT
        row.save(update_fields=['text'])


class Migration(migrations.Migration):
    dependencies = [
        ('prompts', '0003_screening_questions_specific_prompt'),
    ]
    operations = [
        migrations.RunPython(update_prompt, revert_prompt),
    ]
