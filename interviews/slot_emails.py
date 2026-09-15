"""Emails for the interviewer-proposes-slots flow (InterviewRequest). Sent
via the Send-Email-Notifier Logic App (see candidates/logic_app_mail.py) -
always paired with an in-app notifications.services.notify() call at the
call site, never sent alone.
"""
from django.utils import timezone

from email_templates.store import render_email

from candidates import logic_app_mail

# Fallback defaults, used only if the matching EmailTemplate row is missing
# (e.g. before migrations run) - the seeded rows
# (email_templates/migrations/0002_seed_defaults.py) carry this same text,
# and are what an admin actually edits at /admin/.
_NEW_REQUEST_SUBJECT = 'New interview to schedule - {candidate_name} ({round_type})'
_NEW_REQUEST_BODY = (
    'Hello {interviewer_name},\n\n'
    'You have been assigned to interview {candidate_name} for {role} ({round_type}).\n\n'
    'Please sign in to the Interviewer portal and propose 2-3 one-hour slots you are '
    'available, so HR can pick one and schedule the interview.\n\n'
    '{login_url_line}'
    'Regards,\n'
    'HireB'
)
_SLOTS_PROPOSED_SUBJECT = 'Interview slots proposed - {candidate_name} ({round_type})'
_SLOTS_PROPOSED_BODY = (
    'Hello {hr_name},\n\n'
    '{interviewer_name} has proposed the following slots to interview {candidate_name} '
    '({round_type}):\n\n'
    '{slot_lines}\n\n'
    "Please pick one from the candidate's profile to confirm the interview.\n\n"
    'Regards,\n'
    'HireB'
)
_NEW_SLOTS_NEEDED_SUBJECT = 'New slots needed - {candidate_name} ({round_type})'
_NEW_SLOTS_NEEDED_BODY = (
    'Hello {interviewer_name},\n\n'
    'The slots you proposed for {candidate_name} ({round_type}) '
    "don't work for HR. Please propose a fresh set of 2-3 one-hour slots.{note_line}\n\n"
    '{login_url_line}'
    'Regards,\n'
    'HireB'
)


def _role_name(candidate):
    if candidate.job and candidate.job.title:
        return candidate.job.title
    return candidate.role_applied or 'the role'


def notify_interviewer_new_request(interview_request, login_url=''):
    """Sent to the interviewer right after HR allocates them to a
    candidate - asks them to propose 2-3 one-hour slots."""
    interviewer = interview_request.interviewer
    if not interviewer.email:
        return
    candidate = interview_request.candidate
    login_url_line = f'Sign in here: {login_url}\n\n' if login_url else ''
    subject, body = render_email(
        'interviewer_new_request', _NEW_REQUEST_SUBJECT, _NEW_REQUEST_BODY,
        interviewer_name=interviewer.get_full_name() or interviewer.get_username(),
        candidate_name=candidate.full_name, role=_role_name(candidate),
        round_type=interview_request.get_round_type_display(), login_url_line=login_url_line)
    logic_app_mail.send_email(
        to_email=interviewer.email, cc_emails=logic_app_mail.default_cc_list(), subject=subject, body=body)


def notify_hr_slots_proposed(interview_request):
    """Sent to whoever allocated the interviewer, once the interviewer has
    proposed their slots - asks HR to pick one."""
    hr_user = interview_request.created_by
    if not (hr_user and hr_user.email):
        return
    candidate = interview_request.candidate
    interviewer_name = interview_request.interviewer.get_full_name() or interview_request.interviewer.get_username()
    slot_lines = '\n'.join(
        f'- {timezone.localtime(s.start_datetime):%d %b %Y, %H:%M}' for s in interview_request.slots.all())
    subject, body = render_email(
        'hr_slots_proposed', _SLOTS_PROPOSED_SUBJECT, _SLOTS_PROPOSED_BODY,
        hr_name=hr_user.get_full_name() or hr_user.get_username(), interviewer_name=interviewer_name,
        candidate_name=candidate.full_name, round_type=interview_request.get_round_type_display(),
        slot_lines=slot_lines)
    logic_app_mail.send_email(
        to_email=hr_user.email, cc_emails=logic_app_mail.default_cc_list(), subject=subject, body=body)


def notify_interviewer_new_slots_needed(interview_request, note='', login_url=''):
    """Sent to the interviewer when HR asks for a fresh set of slots because
    none of the proposed ones worked."""
    interviewer = interview_request.interviewer
    if not interviewer.email:
        return
    candidate = interview_request.candidate
    note_line = f'\n\nNote from HR: {note}' if note else ''
    login_url_line = f'Sign in here: {login_url}\n\n' if login_url else ''
    subject, body = render_email(
        'interviewer_new_slots_needed', _NEW_SLOTS_NEEDED_SUBJECT, _NEW_SLOTS_NEEDED_BODY,
        interviewer_name=interviewer.get_full_name() or interviewer.get_username(),
        candidate_name=candidate.full_name, round_type=interview_request.get_round_type_display(),
        note_line=note_line, login_url_line=login_url_line)
    logic_app_mail.send_email(
        to_email=interviewer.email, cc_emails=logic_app_mail.default_cc_list(), subject=subject, body=body)
