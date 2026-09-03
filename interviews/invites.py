"""Builds and sends the interview invite email, with a .ics calendar
attachment so it lands in Outlook as a real meeting (Accept/Decline), not
just a plain email.

The subject/body computed here are only a *starting point* - the HR user
reviews and can edit both in the popup before sending, so send_invite()
takes whatever text is actually submitted rather than recomputing it.
Recipient/CC are always derived server-side (never trusted from the
client), since who gets CC'd is a fixed business rule, not user input.
"""
from datetime import timedelta

from django.conf import settings
from django.core.mail import EmailMessage
from django.utils import timezone
from django.utils.dateformat import format as django_date_format

from icalendar import Calendar, Event, vCalAddress, vText

MODE_WORDING = {
    'VIDEO': 'an online video interview',
    'PHONE': 'a phone interview',
    'ONSITE': 'an in-person interview at our office',
}


def default_subject(interview):
    role = _role_name(interview)
    return f'Interview Invitation - {role} ({interview.get_round_type_display()})'


def default_body(interview, sender):
    candidate = interview.candidate
    role = _role_name(interview)
    when = timezone.localtime(interview.scheduled_date)
    date_str = django_date_format(when, 'jS F Y')
    time_str = django_date_format(when, 'g.i A')
    interview_kind = MODE_WORDING.get(interview.mode, 'an interview')
    sender_name = sender.get_full_name() or sender.get_username()

    lines = [
        f'Hello {candidate.full_name},',
        'Greetings from TurnB!',
        f'We are pleased to invite you for {interview_kind} for the position of {role}.',
        'Please find the interview details below:',
        f'Date: {date_str}',
        f'Time: {time_str}',
    ]
    if interview.meeting_link:
        lines.append(f'Join link: {interview.meeting_link}')
    lines += [
        'Please confirm your availability by accepting the invite. Also, ensure a stable internet '
        'connection and a quiet environment for the interview.',
        'Looking forward to speaking with you.',
        '',
        'Regards,',
        sender_name,
        'HR Business Partner',
        'TurnB Business Services Pvt. Ltd – Edapally, Kochi',
        '(+91 9567558557)',
        'http://www.turnb.com/',
    ]
    return '\n'.join(lines)


def default_cc_list(interview):
    """Fixed HR address + the selected interviewer, per the invite policy -
    not user-editable, so a click can't accidentally drop either."""
    cc = []
    fixed = getattr(settings, 'INTERVIEW_INVITE_CC_EMAIL', '')
    if fixed:
        cc.append(fixed)
    if interview.interviewer and interview.interviewer.email and interview.interviewer.email not in cc:
        cc.append(interview.interviewer.email)
    return cc


def _role_name(interview):
    candidate = interview.candidate
    if candidate.job and candidate.job.title:
        return candidate.job.title
    return candidate.role_applied or 'the role'


def _build_ics(interview, *, organizer_email, attendee_emails, summary, description):
    cal = Calendar()
    cal.add('prodid', '-//HireB//Interview Invite//EN')
    cal.add('version', '2.0')
    cal.add('method', 'REQUEST')

    event = Event()
    event.add('summary', summary)
    event.add('dtstart', interview.scheduled_date)
    event.add('dtend', interview.scheduled_date + timedelta(minutes=45))
    event.add('dtstamp', timezone.now())
    event.add('uid', f'interview-{interview.pk}@turnb.com')
    description = description
    if interview.meeting_link:
        description = f'{description}\n\nJoin: {interview.meeting_link}'
        event.add('location', interview.meeting_link)
    event.add('description', description)

    organizer = vCalAddress(f'MAILTO:{organizer_email}')
    organizer.params['cn'] = vText('TurnB Careers')
    event['organizer'] = organizer

    for email in attendee_emails:
        attendee = vCalAddress(f'MAILTO:{email}')
        attendee.params['role'] = vText('REQ-PARTICIPANT')
        attendee.params['partstat'] = vText('NEEDS-ACTION')
        attendee.params['rsvp'] = vText('TRUE')
        event.add('attendee', attendee, encode=0)

    cal.add_component(event)
    return cal.to_ical()


def send_invite(interview, *, to_email, cc_emails, subject, body, sender):
    """Send the invite. Raises on failure - the caller decides how to
    surface that (never silently swallowed, unlike the old placeholder)."""
    from_email = getattr(settings, 'INTERVIEW_INVITE_FROM_EMAIL', None) or settings.DEFAULT_FROM_EMAIL
    ics_bytes = _build_ics(
        interview, organizer_email=from_email,
        attendee_emails=[to_email] + list(cc_emails),
        summary=subject, description=body,
    )
    email = EmailMessage(
        subject=subject, body=body, from_email=from_email,
        to=[to_email], cc=list(cc_emails),
    )
    email.attach('interview-invite.ics', ics_bytes, 'text/calendar; method=REQUEST; charset=UTF-8')
    email.send(fail_silently=False)
