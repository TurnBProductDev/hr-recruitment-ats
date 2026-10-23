from django.db import migrations

# Sent to HR/the recruiter when an interviewer marks a result in the portal -
# see interviews/slot_emails.py::notify_hr_result_recorded, which carries the
# same text as its fallback default.
EMAILS = [
    dict(
        key='hr_interview_result_recorded', label='HR: Interviewer Recorded a Result',
        description=(
            "Sent to whoever allocated/scheduled the interview when the interviewer marks a result - "
            'their pick is a recommendation HR confirms. Placeholders: {hr_name}, {interviewer_name}, '
            '{candidate_name}, {role}, {round_type}, {recommendation}, {score}, {feedback}, {profile_url}.'
        ),
        subject='Interview result - {candidate_name} ({round_type}): {recommendation}',
        body=(
            'Hello {hr_name},\n\n'
            '{interviewer_name} has recorded the result of the {round_type} interview with {candidate_name} '
            'for {role}.\n\n'
            'Recommendation: {recommendation}\n'
            'Interview score: {score}\n'
            'Feedback: {feedback}\n\n'
            "Please confirm the next step (Cleared / Hold / Reject) from the candidate's profile:\n"
            '{profile_url}\n\n'
            'Regards,\n'
            'TurnB ATS'
        ),
    ),
]


def seed(apps, schema_editor):
    EmailTemplate = apps.get_model('email_templates', 'EmailTemplate')
    for entry in EMAILS:
        EmailTemplate.objects.get_or_create(key=entry['key'], defaults=entry)


def unseed(apps, schema_editor):
    EmailTemplate = apps.get_model('email_templates', 'EmailTemplate')
    EmailTemplate.objects.filter(key__in=[e['key'] for e in EMAILS]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ('email_templates', '0005_seed_interview_cancelled_email'),
    ]
    operations = [
        migrations.RunPython(seed, unseed),
    ]
