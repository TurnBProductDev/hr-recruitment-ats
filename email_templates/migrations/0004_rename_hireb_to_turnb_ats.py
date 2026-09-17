from django.db import migrations

# App was renamed HireB -> TurnB ATS. Only touches rows whose subject/body
# still contain the literal old brand name, so a row an admin has already
# customised (and no longer mentions "HireB") is left alone.
RENAMES = {
    'interviewer_new_request': (
        'New interview to schedule - {candidate_name} ({round_type})',
        'Hello {interviewer_name},\n\n'
        'You have been assigned to interview {candidate_name} for {role} ({round_type}).\n\n'
        'Please sign in to the Interviewer portal and propose 2-3 one-hour slots you are '
        'available, so HR can pick one and schedule the interview.\n\n'
        '{login_url_line}'
        'Regards,\n'
        'HireB',
    ),
    'hr_slots_proposed': (
        'Interview slots proposed - {candidate_name} ({round_type})',
        'Hello {hr_name},\n\n'
        '{interviewer_name} has proposed the following slots to interview {candidate_name} '
        '({round_type}):\n\n'
        '{slot_lines}\n\n'
        "Please pick one from the candidate's profile to confirm the interview.\n\n"
        'Regards,\n'
        'HireB',
    ),
    'interviewer_new_slots_needed': (
        'New slots needed - {candidate_name} ({round_type})',
        'Hello {interviewer_name},\n\n'
        'The slots you proposed for {candidate_name} ({round_type}) '
        "don't work for HR. Please propose a fresh set of 2-3 one-hour slots.{note_line}\n\n"
        '{login_url_line}'
        'Regards,\n'
        'HireB',
    ),
    'password_reset': (
        'Reset your HireB password',
        'Hello {name},\n\n'
        'We received a request to reset your HireB password. Click the link below to '
        'choose a new one:\n\n'
        '{reset_url}\n\n'
        "If you didn't request this, you can safely ignore this email - your password "
        "won't be changed.\n\n"
        'Regards,\n'
        'HireB',
    ),
    'hr_candidate_selected_slot': (
        '{candidate_name} selected a slot - {round_type}',
        'Hello {hr_name},\n\n'
        '{candidate_name} has selected the following time for their {round_type} interview:\n\n'
        '{selected_slot}\n\n'
        "This has been recorded against the candidate's Round 1 stage. Please review and either "
        "Approve & Send the invite, or ask the interviewer to Reschedule, from the candidate's "
        'profile.\n\n'
        '{profile_url}\n\n'
        'Regards,\n'
        'HireB',
    ),
}


def rename(apps, schema_editor):
    EmailTemplate = apps.get_model('email_templates', 'EmailTemplate')
    for key, (old_subject, old_body) in RENAMES.items():
        row = EmailTemplate.objects.filter(key=key).first()
        if row is None:
            continue
        if row.subject == old_subject:
            row.subject = old_subject.replace('HireB', 'TurnB ATS')
        if row.body == old_body:
            row.body = old_body.replace('HireB', 'TurnB ATS')
        row.save(update_fields=['subject', 'body'])


def unrename(apps, schema_editor):
    EmailTemplate = apps.get_model('email_templates', 'EmailTemplate')
    for key, (old_subject, old_body) in RENAMES.items():
        row = EmailTemplate.objects.filter(key=key).first()
        if row is None:
            continue
        new_subject = old_subject.replace('HireB', 'TurnB ATS')
        new_body = old_body.replace('HireB', 'TurnB ATS')
        if row.subject == new_subject:
            row.subject = old_subject
        if row.body == new_body:
            row.body = old_body
        row.save(update_fields=['subject', 'body'])


class Migration(migrations.Migration):
    dependencies = [
        ('email_templates', '0003_seed_slot_pick_emails'),
    ]
    operations = [
        migrations.RunPython(rename, unrename),
    ]
