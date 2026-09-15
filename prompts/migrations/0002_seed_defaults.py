from django.db import migrations

from prompts.cv_extraction import SYSTEM_PROMPT as CV_EXTRACTION_PROMPT
from prompts.jd_extraction import SYSTEM_PROMPT as JD_EXTRACTION_PROMPT
from prompts.match_scoring import SYSTEM_PROMPT as MATCH_SCORING_PROMPT
from prompts.profile_extraction import SYSTEM_PROMPT as PROFILE_EXTRACTION_PROMPT
from prompts.screening_questions import system_prompt as _screening_system_prompt

# {question_count} stays a literal token in the seeded text - reusing the
# same function candidates/screening_questions.py calls, just handing it the
# placeholder string instead of a real number (see candidates/screening_
# questions.py's own SYSTEM_PROMPT construction for why this is safe).
SCREENING_QUESTIONS_PROMPT = _screening_system_prompt('{question_count}')

PROMPTS = [
    dict(
        key='match_scoring', label='Candidate-to-Job Scoring', text=MATCH_SCORING_PROMPT,
        description=(
            "Used by Score Candidates to judge how well a candidate fits a role. Must keep "
            "{must_have_block} (the job's must-have requirements) and {extra_criteria_block} "
            "(any extra scoring criteria HR set for that job) somewhere in the text."
        ),
    ),
    dict(
        key='cv_extraction', label='CV Data Extraction', text=CV_EXTRACTION_PROMPT,
        description='Used by Bulk Upload CV to pull structured fields out of an uploaded resume. No placeholders.',
    ),
    dict(
        key='jd_extraction', label='Job Description Extraction', text=JD_EXTRACTION_PROMPT,
        description=(
            'Used when a JD file is uploaded on the Vacancy form, to split it into description/'
            'requirements. No placeholders.'
        ),
    ),
    dict(
        key='profile_extraction', label='Profile Backfill from CV Summary', text=PROFILE_EXTRACTION_PROMPT,
        description='Used to backfill blank profile fields from the AI CV summary. No placeholders.',
    ),
    dict(
        key='screening_questions', label='Tele-Screening Questions', text=SCREENING_QUESTIONS_PROMPT,
        description=(
            'Used to generate Tele Screening call questions from a candidate profile. Must keep '
            '{question_count} somewhere in the text.'
        ),
    ),
]


def seed(apps, schema_editor):
    PromptTemplate = apps.get_model('prompts', 'PromptTemplate')
    for entry in PROMPTS:
        PromptTemplate.objects.get_or_create(key=entry['key'], defaults=entry)


def unseed(apps, schema_editor):
    PromptTemplate = apps.get_model('prompts', 'PromptTemplate')
    PromptTemplate.objects.filter(key__in=[e['key'] for e in PROMPTS]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ('prompts', '0001_initial'),
    ]
    operations = [
        migrations.RunPython(seed, unseed),
    ]
