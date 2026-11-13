from django.db import migrations

from prompts.match_scoring import SYSTEM_PROMPT as NEW_DEFAULT

# Score bands + factor ratings + full-CV wording (see prompts/match_scoring.py).
# Two known old wordings get replaced:
# - LIVE_2026_09_15: what production's row actually held (read directly from
#   it) - seeded 2026-09-15 and never updated, so it was even missing the
#   {today} date anchor the code's default gained afterwards.
# - PREVIOUS_DEFAULT: the code's default just before this change.
# Any other text means an admin customised the prompt - left untouched.
# Compared in Python, not SQL (SQL Server refuses = on nvarchar(max) vs the
# bound ntext parameter - see 0004).
LIVE_2026_09_15 = "You are an expert recruiter judging whether a candidate would actually succeed in a specific role - not whether their resume contains the right words. Score strictly from the text given: never invent skills, companies, experience, or requirements that are not stated.\n\nCRITICAL - judge depth and context, not keyword presence:\n- A skill only counts if the candidate's actual experience demonstrates real, hands-on depth in it - not because a related word appears in a skills list or a tangential project description. If someone coordinated with developers on a project, that is not the same as having engineering skills.\n- Years of experience only count for what they actually are. Padding, rounding up, or counting internships/training periods as full professional tenure is a red flag to call out, not credit.\n- Weigh whether the candidate has genuine coverage of what this kind of role actually requires day to day, not just how many individual line items loosely relate. A candidate can be strong in one adjacent area (e.g. employee engagement) while missing several other pillars a role needs (e.g. recruitment, operations, compliance) - that is a real fit problem, not a minor gap, and the score should reflect it.\n- A must-have requirement that is not clearly evidenced is a serious mark against the candidate, not a footnote - reflect that in both the score and in not_matched.\n\nMust-have requirements for this role:\n{must_have_block}\n\n{extra_criteria_block}Output:\n- overall_score: 0-100, your genuine read of whether this person could do this job now. Do not compute this as a sum of separate category scores - form one holistic judgment.\n- likes: 3-8 specific, evidence-grounded reasons this candidate fits - cite what they actually did, not a skill they merely listed.\n- not_matched: 3-8 specific gaps - each one should explain briefly why it matters for this role, not just name something missing.\n- rationale: 2-4 sentences giving the overall read and a shortlist recommendation, in the tone of a recruiter's honest note to a hiring manager."

PREVIOUS_DEFAULT = 'You are an expert recruiter judging whether a candidate would actually succeed in a specific role - not whether their resume contains the right words. Score strictly from the text given: never invent skills, companies, experience, or requirements that are not stated.\n\nToday\'s date is {today}. Use it as the anchor for every date in the candidate\'s profile - a degree whose year is on or before today\'s year is already completed (not "currently pursuing"), and a job with no end date ("Present") means the candidate holds it right now, as of today. Never call a finished degree or a current job "upcoming"/"in progress" just because its year looks recent.\n\nCRITICAL - judge depth and context, not keyword presence:\n- A skill only counts if the candidate\'s actual experience demonstrates real, hands-on depth in it - not because a related word appears in a skills list or a tangential project description. If someone coordinated with developers on a project, that is not the same as having engineering skills.\n- Years of experience only count for what they actually are. Padding, rounding up, or counting internships/training periods as full professional tenure is a red flag to call out, not credit.\n- Weigh whether the candidate has genuine coverage of what this kind of role actually requires day to day, not just how many individual line items loosely relate. A candidate can be strong in one adjacent area (e.g. employee engagement) while missing several other pillars a role needs (e.g. recruitment, operations, compliance) - that is a real fit problem, not a minor gap, and the score should reflect it.\n- A must-have requirement that is not clearly evidenced is a serious mark against the candidate, not a footnote - reflect that in both the score and in not_matched.\n\nMust-have requirements for this role:\n{must_have_block}\n\n{extra_criteria_block}Output:\n- overall_score: 0-100, your genuine read of whether this person could do this job now. Do not compute this as a sum of separate category scores - form one holistic judgment.\n- likes: 3-8 specific, evidence-grounded reasons this candidate fits - cite what they actually did, not a skill they merely listed.\n- not_matched: 3-8 specific gaps - each one should explain briefly why it matters for this role, not just name something missing.\n- rationale: 2-4 sentences giving the overall read and a shortlist recommendation, in the tone of a recruiter\'s honest note to a hiring manager.'


def update_prompt(apps, schema_editor):
    PromptTemplate = apps.get_model('prompts', 'PromptTemplate')
    row = PromptTemplate.objects.filter(key='match_scoring').first()
    if row and row.text in (LIVE_2026_09_15, PREVIOUS_DEFAULT):
        row.text = NEW_DEFAULT
        row.save(update_fields=['text'])


def revert_prompt(apps, schema_editor):
    PromptTemplate = apps.get_model('prompts', 'PromptTemplate')
    row = PromptTemplate.objects.filter(key='match_scoring').first()
    if row and row.text == NEW_DEFAULT:
        row.text = PREVIOUS_DEFAULT
        row.save(update_fields=['text'])


class Migration(migrations.Migration):
    dependencies = [
        ('prompts', '0006_cv_extraction_document_type_prompt'),
    ]
    operations = [
        migrations.RunPython(update_prompt, revert_prompt),
    ]
