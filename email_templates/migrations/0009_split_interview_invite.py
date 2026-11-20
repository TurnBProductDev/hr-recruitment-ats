from django.db import migrations

# The interview invite is split by mode: the existing row becomes the Online
# version (Video/Phone) - its wording, including any edits already made, is
# left exactly as it is; only its name changes - and a new Offline version
# (Onsite) is added: no join link, no "stable internet connection" line,
# the office address instead. Same defaults as interviews/invites.py.
OFFLINE_KEY = 'interview_invite_offline'
PLACEHOLDERS = ('{candidate_name}, {role}, {round_type}, {interview_kind}, {date_str}, {time_str}, '
                '{sender_name}')
OFFLINE_BODY = (
    'Hello {candidate_name},\n'
    'Greetings from TurnB!\n'
    'We are pleased to invite you for {interview_kind} for the position of {role}.\n'
    'Please find the interview details below:\n'
    'Date: {date_str}\n'
    'Time: {time_str}\n'
    'Location:\n'
    'TurnB\n'
    'Level 3, Oberon Mall, Edapally, Cochin\n'
    'Please confirm your availability by accepting the invite.\n'
    'Looking forward to meeting you.\n'
    '\n'
    'Regards,\n'
    '{sender_name}\n'
    'HR Business Partner\n'
    'TurnB Business Services Pvt. Ltd – Edapally, Kochi\n'
    '(+91 9567558557)\n'
    'http://www.turnb.com/'
)


def forwards(apps, schema_editor):
    EmailTemplate = apps.get_model('email_templates', 'EmailTemplate')
    EmailTemplate.objects.filter(key='interview_invite').update(
        label='Interview Invite (Online)',
        description=('Sent to the candidate for a Video or Phone interview, after HR schedules/reschedules it. '
                     'Placeholders: ' + PLACEHOLDERS + ', {meeting_link_line} (a whole ready "Join link: ..." '
                     'line, or blank if there is no meeting link).'),
    )
    EmailTemplate.objects.get_or_create(key=OFFLINE_KEY, defaults={
        'label': 'Interview Invite (Offline)',
        'description': ('Sent to the candidate for an Onsite (in-person) interview, after HR schedules/reschedules '
                        'it - shows the office location instead of a join link. Placeholders: ' + PLACEHOLDERS
                        + ', {location} (the office address).'),
        'subject': 'Interview Invitation - {role} ({round_type})',
        'body': OFFLINE_BODY,
    })


def backwards(apps, schema_editor):
    EmailTemplate = apps.get_model('email_templates', 'EmailTemplate')
    EmailTemplate.objects.filter(key=OFFLINE_KEY).delete()
    EmailTemplate.objects.filter(key='interview_invite').update(label='Interview Invite')


class Migration(migrations.Migration):
    dependencies = [
        ('email_templates', '0008_seed_co_interviewer_email'),
    ]
    operations = [
        migrations.RunPython(forwards, backwards),
    ]
