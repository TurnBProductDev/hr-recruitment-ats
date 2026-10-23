from django.db import migrations

# Sent to the interviewer when HR cancels an interview at any stage - see
# interviews/slot_emails.py::notify_interviewer_cancelled, which carries the
# same text as its fallback default.
EMAILS = [
    dict(
        key='interviewer_interview_cancelled', label='Interviewer: Interview Cancelled',
        description=(
            'Sent to the interviewer when HR cancels an interview - before slots are proposed, '
            'while the candidate is picking, or after it was scheduled. Placeholders: '
            '{interviewer_name}, {candidate_name}, {role}, {round_type}, {when_line}, {note_line}.'
        ),
        subject='Interview cancelled - {candidate_name} ({round_type})',
        body=(
            'Hello {interviewer_name},\n\n'
            'The {round_type} interview with {candidate_name} for {role}{when_line} has been '
            'cancelled by HR.{note_line}\n\n'
            'If it was on your calendar, the meeting has been withdrawn. No action is needed from you.\n\n'
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
        ('email_templates', '0004_rename_hireb_to_turnb_ats'),
    ]
    operations = [
        migrations.RunPython(seed, unseed),
    ]
