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
    # Compare in Python, not SQL - filtering on text=OLD_DEFAULT can crash SQL
    # Server on this backend for a long enough string ("the data types
    # nvarchar(max) and ntext are incompatible in the equal to operator",
    # ODBC Driver 18 - see migration 0004, which hit this at 1842 chars).
    # Only overwrite a row still at the old wording - an admin who already
    # customised this prompt keeps their own text untouched.
    row = PromptTemplate.objects.filter(key='screening_questions').first()
    if row and row.text == OLD_DEFAULT:
        row.text = NEW_DEFAULT
        row.save(update_fields=['text'])


def revert_prompt(apps, schema_editor):
    PromptTemplate = apps.get_model('prompts', 'PromptTemplate')
    row = PromptTemplate.objects.filter(key='screening_questions').first()
    if row and row.text == NEW_DEFAULT:
        row.text = OLD_DEFAULT
        row.save(update_fields=['text'])


class Migration(migrations.Migration):
    dependencies = [
        ('prompts', '0002_seed_defaults'),
    ]
    operations = [
        migrations.RunPython(update_prompt, revert_prompt),
    ]
