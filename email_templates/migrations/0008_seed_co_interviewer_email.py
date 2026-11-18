from django.db import migrations

# Heads-up to the optional second interviewer when HR allocates them - same
# wording as the defaults in interviews/slot_emails.py, editable at /admin/.
KEY = 'co_interviewer_new_request'
SUBJECT = 'You are Interviewer 2 - {candidate_name} ({round_type})'
BODY = (
    'Hello {co_interviewer_name},\n\n'
    'You have been added as the second interviewer for {candidate_name} - {role} ({round_type}), '
    'alongside {interviewer_name}.\n\n'
    '{interviewer_name} will propose the interview times. Once the candidate picks one, you will '
    'both receive the Outlook meeting invite and the time will be blocked on your calendar.\n\n'
    'You can view the candidate and their CV in the Interviewer portal.\n\n'
    '{login_url_line}'
    'Regards,\n'
    'TurnB ATS'
)


def forwards(apps, schema_editor):
    EmailTemplate = apps.get_model('email_templates', 'EmailTemplate')
    EmailTemplate.objects.get_or_create(key=KEY, defaults={
        'label': 'Interviewer 2 allocated',
        'description': 'Sent to the optional second interviewer when HR allocates them. '
                       'Placeholders: {co_interviewer_name}, {interviewer_name}, {candidate_name}, '
                       '{role}, {round_type}, {login_url_line}.',
        'subject': SUBJECT, 'body': BODY,
    })


def backwards(apps, schema_editor):
    apps.get_model('email_templates', 'EmailTemplate').objects.filter(key=KEY).delete()


class Migration(migrations.Migration):
    dependencies = [
        ('email_templates', '0007_thirty_minute_slots'),
    ]
    operations = [
        migrations.RunPython(forwards, backwards),
    ]
