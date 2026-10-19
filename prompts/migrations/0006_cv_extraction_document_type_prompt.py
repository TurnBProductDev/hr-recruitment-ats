from django.db import migrations

from prompts.cv_extraction import SYSTEM_PROMPT as NEW_DEFAULT

# The cv_extraction prompt exactly as 0004 left it (confirmed identical to
# production's row before this change) - rule 12 / document_type is new here,
# so the careers-mailbox intake and Bulk Upload CV can tell a CV from a cover
# letter or an unrelated PDF (brochure, certificate, ...). Same "only replace
# an untouched default" rule as 0004 - compared in Python, not SQL, for the
# same nvarchar(max)/ntext reason given there.
OLD_DEFAULT = (
    "You are an expert HR data extraction specialist. Read the candidate's CV (given as text, or as page "
    'images if the text layer was unreadable) and extract structured fields for an ATS.\n'
    '\n'
    'CRITICAL RULES:\n'
    '1. Use only what the CV actually states - never invent, guess, or infer a value that is not clearly '
    'supported by the text. Return null for anything not stated.\n'
    '2. Email: lowercase, no internal spaces.\n'
    '3. Mobile: format as +91-XXXXXXXXXX (Indian) or +[CountryCode]-XXXXXXXXX.\n'
    "4. qualification: highest/most recent degree, formatted 'Degree - Institution - Year'.\n"
    "5. skills: a comma-separated list of the candidate's key technical/professional skills, drawn from "
    'anywhere in the CV (a Skills section, project descriptions, work experience bullets).\n'
    '6. experience: one entry per job, most recent first, with the skills/tools actually used in that '
    'role - not a repeat of the whole top-level skills list.\n'
    "6b. education: one entry per degree/qualification listed on the CV (school, diploma, bachelor's, "
    "master's, etc.), most recent first - list ALL of them, not just the highest. The top-level "
    "'qualification' field should still mirror the highest/most recent entry here.\n"
    '7. total_experience_years: total professional experience as a decimal number (e.g. 5.5), not '
    'per-job.\n'
    '8. role_applied: the job title the candidate is applying for, from a subject line/cover note if '
    'given, else null - do not guess it from their current job title.\n'
    "9. source: only from the email context (subject/sender), never from the CV itself - 'Linked In' if "
    "it mentions LinkedIn, 'Referral' if a referral, 'Indeed' if from Indeed, 'Naukri' if from Naukri, "
    "else 'Careers'. Null if there's no email context at all (e.g. a bulk upload with no email "
    'involved).\n'
    '10. summary: 4 short Markdown sections - Candidate Overview, Core Technical Competencies (a table), '
    'Professional Experience Highlights (bullets), and Education/Logistics - matching the tone of an '
    "experienced HR analyst's notes. Base it strictly on the CV text; do not invent achievements.\n"
    '11. matched_job_title: if a list of open vacancies is given below, pick the ONE that this '
    "application is clearly for - same role by meaning, even if worded differently (e.g. 'HR Role' can "
    "mean 'HRBP' if that's the only HR-shaped opening given; 'Sales Associate - Analytics & AI' means "
    "the 'Sales Associate' opening, not a made-up combination). Return null if nothing on the list is "
    'clearly the same role, or if no list is given at all - a wrong match is worse than leaving this '
    'null, so when genuinely unsure, return null rather than guessing from superficial keyword overlap '
    "(e.g. 'Accountant' is NOT a match for 'Analytics Consultant' just because both contain similar "
    'letters).\n'
    '\n'
    'Return ONLY the JSON object described by the schema. No markdown formatting, no commentary.'
)


def update_prompt(apps, schema_editor):
    PromptTemplate = apps.get_model('prompts', 'PromptTemplate')
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
        ('prompts', '0005_seed_job_matching'),
    ]
    operations = [
        migrations.RunPython(update_prompt, revert_prompt),
    ]
