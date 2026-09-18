from django.db import migrations

from prompts.job_matching import SYSTEM_PROMPT as JOB_MATCHING_PROMPT


def seed(apps, schema_editor):
    PromptTemplate = apps.get_model('prompts', 'PromptTemplate')
    PromptTemplate.objects.get_or_create(
        key='job_matching',
        defaults=dict(
            key='job_matching', label='General Application Re-matching', text=JOB_MATCHING_PROMPT,
            description=(
                'Used by the rematch_general_applications management command to re-check a General '
                'Application candidate against currently open vacancies. No placeholders.'
            ),
        ),
    )


def unseed(apps, schema_editor):
    PromptTemplate = apps.get_model('prompts', 'PromptTemplate')
    PromptTemplate.objects.filter(key='job_matching').delete()


class Migration(migrations.Migration):
    dependencies = [
        ('prompts', '0004_cv_extraction_matched_job_prompt'),
    ]
    operations = [
        migrations.RunPython(seed, unseed),
    ]
