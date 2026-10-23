"""Emails for the interviewer-proposes-slots flow (InterviewRequest). Sent
via the Send-Email-Notifier Logic App (see candidates/logic_app_mail.py) -
always paired with an in-app notifications.services.notify() call at the
call site, never sent alone.
"""
from django.utils import timezone

from email_templates.store import render_email

from candidates import logic_app_mail

from .models import candidate_round_label

# Fallback defaults, used only if the matching EmailTemplate row is missing
# (e.g. before migrations run) - the seeded rows
# (email_templates/migrations/0002_seed_defaults.py) carry this same text,
# and are what an admin actually edits at /admin/.
_NEW_REQUEST_SUBJECT = 'New interview to schedule - {candidate_name} ({round_type})'
_NEW_REQUEST_BODY = (
    'Hello {interviewer_name},\n\n'
    'You have been assigned to interview {candidate_name} for {role} ({round_type}).\n\n'
    'Please sign in to the Interviewer portal and propose 2-3 thirty-minute slots you are '
    'available, so HR can pick one and schedule the interview.\n\n'
    '{login_url_line}'
    'Regards,\n'
    'TurnB ATS'
)
_CANDIDATE_SELECT_SLOT_SUBJECT = 'Pick your {round_type} interview time - {role}'
_CANDIDATE_SELECT_SLOT_BODY = (
    'Hello {candidate_name},\n\n'
    'Thank you for your interest in the {role} position. We would like to schedule your '
    '{round_type} interview.\n\n'
    'Please choose a time that works for you using the link below (all times shown in Indian '
    'Standard Time - IST):\n\n'
    '{select_url}\n\n'
    "This link is valid for 48 hours. If you don't pick a time within that window, please reach "
    'out to us so we can share new options.\n\n'
    'Regards\n'
    'HRBP\n'
    'TurnB Business Services Pvt Ltd'
)
_HR_CANDIDATE_SELECTED_SUBJECT = '{candidate_name} selected a slot - {round_type}'
_HR_CANDIDATE_SELECTED_BODY = (
    'Hello {hr_name},\n\n'
    '{candidate_name} has selected the following time for their {round_type} interview:\n\n'
    '{selected_slot}\n\n'
    "This has been recorded against the candidate's Round 1 stage. Please review and either "
    'Approve & Send the invite, or ask the interviewer to Reschedule, from the candidate\'s '
    'profile.\n\n'
    '{profile_url}\n\n'
    'Regards,\n'
    'TurnB ATS'
)
_NEW_SLOTS_NEEDED_SUBJECT = 'New slots needed - {candidate_name} ({round_type})'
_NEW_SLOTS_NEEDED_BODY = (
    'Hello {interviewer_name},\n\n'
    'The slots you proposed for {candidate_name} ({round_type}) '
    "don't work for HR. Please propose a fresh set of 2-3 thirty-minute slots.{note_line}\n\n"
    '{login_url_line}'
    'Regards,\n'
    'TurnB ATS'
)
_INTERVIEW_CANCELLED_SUBJECT = 'Interview cancelled - {candidate_name} ({round_type})'
_INTERVIEW_CANCELLED_BODY = (
    'Hello {interviewer_name},\n\n'
    'The {round_type} interview with {candidate_name} for {role}{when_line} has been cancelled by HR.'
    '{note_line}\n\n'
    'If it was on your calendar, the meeting has been withdrawn. No action is needed from you.\n\n'
    'Regards,\n'
    'TurnB ATS'
)
_RESULT_RECORDED_SUBJECT = 'Interview result - {candidate_name} ({round_type}): {recommendation}'
_RESULT_RECORDED_BODY = (
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
)


def _role_name(candidate):
    if candidate.job and candidate.job.title:
        return candidate.job.title
    return candidate.role_applied or 'the role'


def notify_interviewer_new_request(interview_request, login_url=''):
    """Sent to the interviewer right after HR allocates them to a
    candidate - asks them to propose 2-3 thirty-minute slots."""
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


def notify_candidate_select_slot(interview_request, select_url):
    """Sent to the CANDIDATE once the interviewer has proposed their slots -
    asks them to pick one via the link (a token-secured public page, valid
    for CANDIDATE_SLOT_LINK_HOURS - see InterviewRequest.candidate_token).
    Replaces the old "ask HR to pick" email now that the candidate picks
    directly."""
    candidate = interview_request.candidate
    if not candidate.email or candidate.email_is_placeholder:
        return
    subject, body = render_email(
        'candidate_select_slot', _CANDIDATE_SELECT_SLOT_SUBJECT, _CANDIDATE_SELECT_SLOT_BODY,
        candidate_name=candidate.full_name, role=_role_name(candidate),
        round_type=candidate_round_label(interview_request.round_type), select_url=select_url)
    logic_app_mail.send_email(
        to_email=candidate.email, cc_emails=logic_app_mail.default_cc_list(), subject=subject, body=body)


def notify_hr_candidate_selected(interview_request, profile_url):
    """Sent to whoever allocated the interviewer, once the candidate has
    picked a slot - asks HR to Approve & Send or Reschedule from the
    candidate's profile."""
    hr_user = interview_request.created_by
    if not (hr_user and hr_user.email):
        return
    candidate = interview_request.candidate
    slot = interview_request.candidate_selected_slot
    selected_slot = f'{timezone.localtime(slot.start_datetime):%d %b %Y, %H:%M} IST' if slot else 'Unknown'
    subject, body = render_email(
        'hr_candidate_selected_slot', _HR_CANDIDATE_SELECTED_SUBJECT, _HR_CANDIDATE_SELECTED_BODY,
        hr_name=hr_user.get_full_name() or hr_user.get_username(), candidate_name=candidate.full_name,
        round_type=interview_request.get_round_type_display(), selected_slot=selected_slot,
        profile_url=profile_url)
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


def notify_hr_result_recorded(*, hr_users, interview, recommendation, feedback, profile_url):
    """Sent when an interviewer marks a result in the portal - their pick is
    only a recommendation, so HR/the recruiter who scheduled the interview
    is asked to confirm the next step. Goes to `hr_users` (whoever allocated/
    scheduled it), or to the standing HR address when none has an email."""
    emails = list(dict.fromkeys(u.email for u in hr_users if u and u.email))
    cc = logic_app_mail.default_cc_list()
    if emails:
        to_email, cc = emails[0], emails[1:] + [c for c in cc if c not in emails]
        first = next(u for u in hr_users if u and u.email == to_email)
        hr_name = first.get_full_name() or first.get_username()
    else:
        to_email, cc, hr_name = cc[-1], cc[:-1], 'HR'
    candidate, interviewer = interview.candidate, interview.interviewer
    score = f'{interview.score:g} / 10' if interview.score is not None else 'Not given'
    subject, body = render_email(
        'hr_interview_result_recorded', _RESULT_RECORDED_SUBJECT, _RESULT_RECORDED_BODY,
        hr_name=hr_name, interviewer_name=(interviewer.get_full_name() or interviewer.get_username())
        if interviewer else 'The interviewer',
        candidate_name=candidate.full_name, role=_role_name(candidate),
        round_type=interview.get_round_type_display(), recommendation=recommendation,
        score=score, feedback=feedback or '-', profile_url=profile_url)
    logic_app_mail.send_email(to_email=to_email, cc_emails=cc, subject=subject, body=body)


def notify_interviewer_cancelled(*, interviewer, candidate, round_type, when=None, note=''):
    """Sent to the interviewer when HR cancels an interview - at any stage,
    from a fresh allocation to an approved, scheduled interview. `round_type`
    is the HR-side label ("Round 1"); `when` the interview's time, if one was
    ever fixed."""
    if not (interviewer and interviewer.email):
        return
    when_line = f' on {timezone.localtime(when):%d %b %Y, %H:%M} IST' if when else ''
    note_line = f'\n\nNote from HR: {note}' if note else ''
    subject, body = render_email(
        'interviewer_interview_cancelled', _INTERVIEW_CANCELLED_SUBJECT, _INTERVIEW_CANCELLED_BODY,
        interviewer_name=interviewer.get_full_name() or interviewer.get_username(),
        candidate_name=candidate.full_name, role=_role_name(candidate), round_type=round_type,
        when_line=when_line, note_line=note_line)
    logic_app_mail.send_email(
        to_email=interviewer.email, cc_emails=logic_app_mail.default_cc_list(), subject=subject, body=body)
