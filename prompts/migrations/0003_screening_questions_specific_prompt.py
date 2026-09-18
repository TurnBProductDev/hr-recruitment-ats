from django.db import migrations

from prompts.screening_questions import system_prompt as _screening_system_prompt

# The exact text 0002_seed_defaults.py originally seeded (frozen here, not
# re-imported, since the source function's wording has since changed) - used
# only to check whether a row is still untouched before overwriting it.
OLD_DEFAULT = (
    "You are an experienced recruiter preparing for a first-round telephonic "
    "screening call with a job applicant. Based on the candidate's profile and "
    "the role they applied for, write exactly {question_count} screening "
    "questions to ask them on the call. Cover their background, key skills, "
    "relevant experience, career motivation, notice period/availability, and "
    "salary expectations where appropriate. Keep each question short and "
    "conversational, suited to a phone call - not a technical panel interview. "
    "Base them only on what the profile actually states - never invent specifics "
    "that aren't there."
)

NEW_DEFAULT = _screening_system_prompt('{question_count}')


def update_prompt(apps, schema_editor):
    PromptTemplate = apps.get_model('prompts', 'PromptTemplate')
    # Only overwrite a row still at the old wording - an admin who already
    # customised this prompt keeps their own text untouched.
    PromptTemplate.objects.filter(key='screening_questions', text=OLD_DEFAULT).update(text=NEW_DEFAULT)


def revert_prompt(apps, schema_editor):
    PromptTemplate = apps.get_model('prompts', 'PromptTemplate')
    PromptTemplate.objects.filter(key='screening_questions', text=NEW_DEFAULT).update(text=OLD_DEFAULT)


class Migration(migrations.Migration):
    dependencies = [
        ('prompts', '0002_seed_defaults'),
    ]
    operations = [
        migrations.RunPython(update_prompt, revert_prompt),
    ]
