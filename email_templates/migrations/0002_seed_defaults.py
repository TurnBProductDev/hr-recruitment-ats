from django.db import migrations

# Every subject/body below is transcribed verbatim from today's hardcoded
# f-strings (candidates/rejection_emails.py, interviews/invites.py,
# interviews/slot_emails.py, HR_management/password_forms.py) - only the
# dynamic insertion points became named {tokens}, so the rendered output is
# byte-identical to today until an admin actually edits a row.
EMAILS = [
    dict(
        key='rejection', label='Rejection (Round 1/2/Final Decision)',
        description=(
            'Sent from the Reject button on Round 1, Round 2 and Final Decision. '
            'Placeholders: {candidate_name}.'
        ),
        subject='Update on your application to TurnB Business Services',
        body=(
            'Hello {candidate_name},\n\n'
            'I hope this e-mail finds you well. I wanted to reach out to you personally and thank you '
            'for all the time and energy that you have invested for the interview process at TurnB '
            'Business Services Pvt. Ltd.  We have thoroughly enjoyed getting to know you throughout the '
            'process.\n\n'
            'Our performance bars were high but unfortunately you could not meet those this time. We '
            'have planned to move forward with other candidates this time whose skills closely align '
            'with the specific needs of the position.\n\n'
            'We wish you every success in your future endeavors!\n\n'
            'Regards,\n'
            'HRBP\n'
            'TurnB Business Services Pvt Ltd'
        ),
    ),
    dict(
        key='interview_invite', label='Interview Invite',
        description=(
            'Sent after HR schedules/reschedules an interview - also used as the .ics calendar '
            'attachment\'s summary/description. Placeholders: {candidate_name}, {role}, {round_type}, '
            '{interview_kind}, {date_str}, {time_str}, {meeting_link_line} (a whole ready line, or blank '
            'if no meeting link), {sender_name}.'
        ),
        subject='Interview Invitation - {role} ({round_type})',
        body=(
            'Hello {candidate_name},\n'
            'Greetings from TurnB!\n'
            'We are pleased to invite you for {interview_kind} for the position of {role}.\n'
            'Please find the interview details below:\n'
            'Date: {date_str}\n'
            'Time: {time_str}\n'
            '{meeting_link_line}'
            'Please confirm your availability by accepting the invite. Also, ensure a stable internet '
            'connection and a quiet environment for the interview.\n'
            'Looking forward to speaking with you.\n'
            '\n'
            'Regards,\n'
            '{sender_name}\n'
            'HR Business Partner\n'
            'TurnB Business Services Pvt. Ltd – Edapally, Kochi\n'
            '(+91 9567558557)\n'
            'http://www.turnb.com/'
        ),
    ),
    dict(
        key='interviewer_new_request', label='Interviewer: New Interview to Schedule',
        description=(
            'Sent to the interviewer right after HR allocates them to a candidate. Placeholders: '
            '{interviewer_name}, {candidate_name}, {role}, {round_type}, {login_url_line} (a whole ready '
            'line, or blank if no portal link).'
        ),
        subject='New interview to schedule - {candidate_name} ({round_type})',
        body=(
            'Hello {interviewer_name},\n\n'
            'You have been assigned to interview {candidate_name} for {role} ({round_type}).\n\n'
            'Please sign in to the Interviewer portal and propose 2-3 one-hour slots you are '
            'available, so HR can pick one and schedule the interview.\n\n'
            '{login_url_line}'
            'Regards,\n'
            'HireB'
        ),
    ),
    dict(
        key='hr_slots_proposed', label='HR: Interview Slots Proposed',
        description=(
            'Sent to whoever allocated the interviewer, once the interviewer has proposed their slots. '
            'Placeholders: {hr_name}, {interviewer_name}, {candidate_name}, {round_type}, {slot_lines} '
            '(one "- date, time" line per proposed slot).'
        ),
        subject='Interview slots proposed - {candidate_name} ({round_type})',
        body=(
            'Hello {hr_name},\n\n'
            '{interviewer_name} has proposed the following slots to interview {candidate_name} '
            '({round_type}):\n\n'
            '{slot_lines}\n\n'
            "Please pick one from the candidate's profile to confirm the interview.\n\n"
            'Regards,\n'
            'HireB'
        ),
    ),
    dict(
        key='interviewer_new_slots_needed', label='Interviewer: New Slots Needed',
        description=(
            'Sent to the interviewer when HR asks for a fresh set of slots because none of the proposed '
            'ones worked. Placeholders: {interviewer_name}, {candidate_name}, {round_type}, {note_line} '
            "(HR's note, pre-formatted, or blank), {login_url_line} (a whole ready line, or blank)."
        ),
        subject='New slots needed - {candidate_name} ({round_type})',
        body=(
            'Hello {interviewer_name},\n\n'
            'The slots you proposed for {candidate_name} ({round_type}) '
            "don't work for HR. Please propose a fresh set of 2-3 one-hour slots.{note_line}\n\n"
            '{login_url_line}'
            'Regards,\n'
            'HireB'
        ),
    ),
    dict(
        key='password_reset', label='Password Reset',
        description='Sent when someone requests a password reset. Placeholders: {name}, {reset_url}.',
        subject='Reset your HireB password',
        body=(
            'Hello {name},\n\n'
            'We received a request to reset your HireB password. Click the link below to '
            'choose a new one:\n\n'
            '{reset_url}\n\n'
            "If you didn't request this, you can safely ignore this email - your password "
            "won't be changed.\n\n"
            'Regards,\n'
            'HireB'
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
        ('email_templates', '0001_initial'),
    ]
    operations = [
        migrations.RunPython(seed, unseed),
    ]
