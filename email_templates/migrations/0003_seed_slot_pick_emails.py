from django.db import migrations

# New templates for the candidate-picks-their-own-slot redesign (replaces the
# old "ask HR to pick a slot" step - see interviews/slot_emails.py). The old
# 'hr_slots_proposed' row is left in place (unused going forward, but not
# deleted - an admin may have already customised it).
EMAILS = [
    dict(
        key='candidate_select_slot', label='Candidate: Pick Your Interview Slot',
        description=(
            'Sent to the candidate once the interviewer has proposed slots - asks them to pick one '
            'via a link, valid for 48 hours. Placeholders: {candidate_name}, {role}, {round_type}, '
            '{select_url}.'
        ),
        subject='Pick your {round_type} interview time - {role}',
        body=(
            'Hello {candidate_name},\n\n'
            'Thank you for your interest in the {role} position. We would like to schedule your '
            '{round_type} interview.\n\n'
            'Please choose a time that works for you using the link below (all times shown in Indian '
            'Standard Time - IST):\n\n'
            '{select_url}\n\n'
            "This link is valid for 48 hours. If you don't pick a time within that window, please "
            'reach out to us so we can share new options.\n\n'
            'Regards\n'
            'HRBP\n'
            'TurnB Business Services Pvt Ltd'
        ),
    ),
    dict(
        key='hr_candidate_selected_slot', label='HR: Candidate Selected Interview Slot',
        description=(
            'Sent to whoever allocated the interviewer, once the candidate has picked a slot - asks '
            'HR to Approve & Send or Reschedule from the profile page. Placeholders: {hr_name}, '
            '{candidate_name}, {round_type}, {selected_slot}, {profile_url}.'
        ),
        subject='{candidate_name} selected a slot - {round_type}',
        body=(
            'Hello {hr_name},\n\n'
            '{candidate_name} has selected the following time for their {round_type} interview:\n\n'
            '{selected_slot}\n\n'
            "This has been recorded against the candidate's Round 1 stage. Please review and either "
            "Approve & Send the invite, or ask the interviewer to Reschedule, from the candidate's "
            'profile.\n\n'
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
        ('email_templates', '0002_seed_defaults'),
    ]
    operations = [
        migrations.RunPython(seed, unseed),
    ]
