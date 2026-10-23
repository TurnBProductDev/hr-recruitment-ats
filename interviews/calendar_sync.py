"""Blocks the interviewer's calendar for an interview, via the
`Interview-Calendar` Azure Logic App (logic_apps/interview_calendar.json).

The Logic App holds no logic of its own: it forwards one Microsoft Graph
calendar call - create / move / cancel an event - through the Office 365
connection already signed in as careers@turnb.com (its "Send an HTTP request"
action), so every interview becomes a real meeting organized by
careers@turnb.com with the interviewer as attendee. Outlook puts it on the
interviewer's calendar straight away (Tentative until they accept); a VIDEO
interview also gets a Teams meeting, whose join URL is handed back for
Interview.meeting_link. No admin-consented Graph app is involved - that is
what interviews/graph_client.py would need, and it stays inert.

When it happens:
- the candidate picks a slot -> block_request_slot (the event lives on the
  InterviewRequest until HR approves it, then carries over to the Interview)
- HR schedules directly / reschedules -> sync_interview (create or move)
- HR cancels, or sends the request back for new slots -> cancel_*_event

Every public helper here is best-effort, like the rest of this app's Azure
integrations: a Logic App outage is logged and the scheduling action still
goes through, exactly as before this module existed. Inert until
LOGIC_APP_CALENDAR_URL is set.
"""
import html
import logging
from datetime import timezone as dt_timezone

import requests
from django.conf import settings
from django.utils import timezone

from .models import INTERVIEW_DURATION, Interview

logger = logging.getLogger(__name__)


class CalendarError(Exception):
    """Raised when the Logic App could not create/move/cancel an event."""


def is_configured():
    return bool(getattr(settings, 'LOGIC_APP_CALENDAR_URL', ''))


def _post(payload):
    try:
        response = requests.post(settings.LOGIC_APP_CALENDAR_URL, json=payload, timeout=(15, 60))
    except requests.RequestException as exc:
        raise CalendarError(f'Could not reach the calendar service: {exc}')
    if response.status_code >= 400:
        raise CalendarError(f'Calendar service returned HTTP {response.status_code}: {response.text[:300]}')
    try:
        return response.json() if response.content else {}
    except ValueError:
        raise CalendarError('Calendar service returned a non-JSON response.')


def _utc(dt):
    return {'dateTime': timezone.localtime(dt, dt_timezone.utc).strftime('%Y-%m-%dT%H:%M:%S'),
            'timeZone': 'UTC'}


def _name(user):
    return user.get_full_name() or user.get_username()


def _event(*, candidate, round_label, interviewer, start, mode, portal_url=''):
    """The Graph event fields shared by create and move. Times go over in
    UTC; Outlook shows them in each attendee's own time zone."""
    role = candidate.job.title if candidate.job_id and candidate.job.title else (candidate.role_applied or '')
    lines = [f'{round_label} interview with <b>{html.escape(candidate.full_name)}</b>'
             + (f' for {html.escape(role)}' if role else '') + '.',
             f'Mode: {html.escape(Interview.Mode(mode).label)}.']
    if portal_url:
        lines.append(f'Candidate profile: <a href="{html.escape(portal_url)}">{html.escape(portal_url)}</a>')
    lines.append('Scheduled through TurnB ATS. Changes are made by HR in the ATS.')
    return {
        'subject': f'Interview: {candidate.full_name} ({round_label})',
        'body': {'contentType': 'HTML', 'content': '<br>'.join(lines)},
        'start': _utc(start),
        'end': _utc(start + INTERVIEW_DURATION),
        'attendees': [{'emailAddress': {'address': interviewer.email, 'name': _name(interviewer)},
                       'type': 'required'}],
        'showAs': 'busy',
        'allowNewTimeProposals': False,
        'responseRequested': True,
    }


def book(*, candidate, round_label, interviewer, start, mode, portal_url='', transaction_id=''):
    """Create the meeting. Returns (event_id, join_url); join_url is '' for a
    non-video interview. Raises CalendarError."""
    event = _event(candidate=candidate, round_label=round_label, interviewer=interviewer,
                   start=start, mode=mode, portal_url=portal_url)
    if mode == Interview.Mode.VIDEO:
        event.update(isOnlineMeeting=True, onlineMeetingProvider='teamsForBusiness')
    if transaction_id:
        # Graph de-duplicates creates carrying the same transactionId, so a
        # retried request can't put two copies on the interviewer's calendar.
        event['transactionId'] = transaction_id
    data = _post({'op': 'create', 'event': event})
    if not data.get('id'):
        raise CalendarError('Calendar service created no event id.')
    return data['id'], data.get('join_url') or ''


def move(event_id, *, candidate, round_label, interviewer, start, mode, portal_url=''):
    """Move/re-assign an existing meeting (Outlook sends the update, and a
    cancellation to an interviewer who was swapped out). Returns its join
    URL. Raises CalendarError."""
    event = _event(candidate=candidate, round_label=round_label, interviewer=interviewer,
                   start=start, mode=mode, portal_url=portal_url)
    data = _post({'op': 'update', 'event_id': event_id, 'event': event})
    return data.get('join_url') or ''


def cancel(event_id, comment=''):
    """Withdraw the meeting - Outlook sends the attendees a cancellation and
    takes it off their calendars. Raises CalendarError."""
    _post({'op': 'cancel', 'event_id': event_id, 'comment': comment})


# --- Best-effort helpers used by the views ------------------------------------

def _can_book(interviewer):
    return is_configured() and interviewer is not None and bool(interviewer.email)


def block_request_slot(interview_request, portal_url=''):
    """The candidate just picked a slot: put it on the interviewer's calendar
    (replacing any earlier block for this request)."""
    slot = interview_request.candidate_selected_slot
    if not slot or not _can_book(interview_request.interviewer):
        return
    if interview_request.calendar_event_id:
        cancel_request_event(interview_request, 'The interview time changed.')
    try:
        event_id, join_url = book(
            candidate=interview_request.candidate, round_label=interview_request.get_round_type_display(),
            interviewer=interview_request.interviewer, start=slot.start_datetime,
            mode=interview_request.mode, portal_url=portal_url,
            transaction_id=f'turnb-ats-request-{interview_request.pk}-slot-{slot.pk}')
    except CalendarError as exc:
        logger.warning('Could not block the calendar for interview request %s: %s', interview_request.pk, exc)
        return
    interview_request.calendar_event_id = event_id
    interview_request.calendar_join_url = join_url
    interview_request.save(update_fields=['calendar_event_id', 'calendar_join_url'])


def cancel_request_event(interview_request, comment=''):
    if not interview_request.calendar_event_id:
        return
    if is_configured():
        try:
            cancel(interview_request.calendar_event_id, comment)
        except CalendarError as exc:
            logger.warning('Could not cancel the calendar event for interview request %s: %s',
                           interview_request.pk, exc)
    interview_request.calendar_event_id = ''
    interview_request.calendar_join_url = ''
    interview_request.save(update_fields=['calendar_event_id', 'calendar_join_url'])


def adopt_request_event(interview, interview_request):
    """HR approved the candidate's pick: the meeting already on the
    interviewer's calendar becomes this interview's, Teams link included."""
    if not interview_request.calendar_event_id:
        return False
    interview.calendar_event_id = interview_request.calendar_event_id
    fields = ['calendar_event_id']
    if (interview.mode == Interview.Mode.VIDEO and not interview.meeting_link
            and interview_request.calendar_join_url):
        interview.meeting_link = interview_request.calendar_join_url
        fields.append('meeting_link')
    interview.save(update_fields=fields)
    return True


def sync_interview(interview, portal_url=''):
    """Create the interview's meeting, or move it to the interview's current
    date/interviewer. Past interviews are left alone."""
    if interview.scheduled_date <= timezone.now() or not _can_book(interview.interviewer):
        return
    kwargs = dict(candidate=interview.candidate, round_label=interview.get_round_type_display(),
                  interviewer=interview.interviewer, start=interview.scheduled_date,
                  mode=interview.mode, portal_url=portal_url)
    try:
        if interview.calendar_event_id:
            join_url = move(interview.calendar_event_id, **kwargs)
        else:
            interview.calendar_event_id, join_url = book(
                **kwargs, transaction_id=f'turnb-ats-interview-{interview.pk}-{timezone.now():%Y%m%d%H%M%S}')
    except CalendarError as exc:
        logger.warning('Could not sync the calendar for interview %s: %s', interview.pk, exc)
        return
    fields = ['calendar_event_id']
    if interview.mode == Interview.Mode.VIDEO and not interview.meeting_link and join_url:
        interview.meeting_link = join_url
        fields.append('meeting_link')
    interview.save(update_fields=fields)


def cancel_interview_event(interview, comment=''):
    if not interview.calendar_event_id:
        return
    if is_configured():
        try:
            cancel(interview.calendar_event_id, comment)
        except CalendarError as exc:
            logger.warning('Could not cancel the calendar event for interview %s: %s', interview.pk, exc)
    interview.calendar_event_id = ''
    interview.save(update_fields=['calendar_event_id'])
