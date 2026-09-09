"""Emails for the interviewer-proposes-slots flow (InterviewRequest). Sent the
same way as interviews/invites.py and candidates/rejection_emails.py - plain
Django EmailMessage, console backend in dev until real SMTP is configured -
and always paired with an in-app notifications.services.notify() call at the
call site, never sent alone.
"""
from django.conf import settings
from django.core.mail import EmailMessage
from django.utils import timezone


def _role_name(candidate):
    if candidate.job and candidate.job.title:
        return candidate.job.title
    return candidate.role_applied or 'the role'


def _from_email():
    return getattr(settings, 'INTERVIEW_INVITE_FROM_EMAIL', None) or settings.DEFAULT_FROM_EMAIL


def notify_interviewer_new_request(interview_request):
    """Sent to the interviewer right after HR allocates them to a
    candidate - asks them to propose 2-3 one-hour slots."""
    interviewer = interview_request.interviewer
    if not interviewer.email:
        return
    candidate = interview_request.candidate
    subject = f'New interview to schedule - {candidate.full_name} ({interview_request.get_round_type_display()})'
    body = (
        f'Hello {interviewer.get_full_name() or interviewer.get_username()},\n\n'
        f'You have been assigned to interview {candidate.full_name} for {_role_name(candidate)} '
        f'({interview_request.get_round_type_display()}).\n\n'
        f'Please sign in to the Interviewer portal and propose 2-3 one-hour slots you are '
        f'available, so HR can pick one and schedule the interview.\n\n'
        f'Regards,\nHireB'
    )
    EmailMessage(subject=subject, body=body, from_email=_from_email(), to=[interviewer.email]).send(fail_silently=False)


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
    subject = f'Interview slots proposed - {candidate.full_name} ({interview_request.get_round_type_display()})'
    body = (
        f'Hello {hr_user.get_full_name() or hr_user.get_username()},\n\n'
        f'{interviewer_name} has proposed the following slots to interview {candidate.full_name} '
        f'({interview_request.get_round_type_display()}):\n\n{slot_lines}\n\n'
        f'Please pick one from the candidate\'s profile to confirm the interview.\n\n'
        f'Regards,\nHireB'
    )
    EmailMessage(subject=subject, body=body, from_email=_from_email(), to=[hr_user.email]).send(fail_silently=False)


def notify_interviewer_new_slots_needed(interview_request, note=''):
    """Sent to the interviewer when HR asks for a fresh set of slots because
    none of the proposed ones worked."""
    interviewer = interview_request.interviewer
    if not interviewer.email:
        return
    candidate = interview_request.candidate
    subject = f'New slots needed - {candidate.full_name} ({interview_request.get_round_type_display()})'
    body = (
        f'Hello {interviewer.get_full_name() or interviewer.get_username()},\n\n'
        f'The slots you proposed for {candidate.full_name} ({interview_request.get_round_type_display()}) '
        f'don\'t work for HR. Please propose a fresh set of 2-3 one-hour slots.'
        f'{chr(10) + chr(10) + "Note from HR: " + note if note else ""}\n\n'
        f'Regards,\nHireB'
    )
    EmailMessage(subject=subject, body=body, from_email=_from_email(), to=[interviewer.email]).send(fail_silently=False)
