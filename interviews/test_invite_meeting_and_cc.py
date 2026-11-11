"""HR feedback round (2026-11-11):
- Hide Reapply keeps one (the newest) application per applicant;
- the invite's CC is editable;
- a video interview always gets its Teams meeting - with or without an
  interviewer - and Send Invite sends that meeting to the candidate + CC, so
  their calendars stay in sync with reschedules.

Run against sqlite so the live Azure DB is never touched:
    DB_ENGINE=sqlite python manage.py test interviews
"""
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from candidates import services
from candidates.models import Candidate, Note
from candidates.permissions import HR_ADMIN
from jobs.models import Job

from .models import Interview

CAL_URL = 'https://calendar.example/trigger'
AJAX = {'HTTP_X_REQUESTED_WITH': 'XMLHttpRequest'}


def _reply(**data):
    response = mock.Mock(status_code=200, content=b'{}')
    response.json.return_value = data
    return response


class _HR(TestCase):
    def setUp(self):
        self.hr = get_user_model().objects.create_user('hr1', 'hr1@turnb.com', 'pw', first_name='Amrita')
        self.hr.groups.add(Group.objects.get_or_create(name=HR_ADMIN)[0])
        self.client.force_login(self.hr)
        self.job = Job.objects.create(job_code='J1', title='Program Manager')


class HideReapplyKeepsOneTests(_HR):
    def _open(self, name, email):
        c = Candidate.objects.create(full_name=name, email=email, job=self.job)
        services.record_creation(c)
        return c

    def _names(self, **params):
        response = self.client.get(reverse('candidate_repository'), {'tab': 'open', 'scoped': '1', **params})
        return {c.full_name for c in response.context['candidates']}

    def test_keeps_the_newest_application_per_applicant(self):
        first = self._open('Riya (first)', 'riya@example.com')
        Candidate.objects.filter(pk=first.pk).update(created_at=timezone.now() - timedelta(days=30))
        self._open('Riya (again)', 'riya@example.com')
        self._open('Joel', 'joel@example.com')
        self.assertEqual(self._names(), {'Riya (first)', 'Riya (again)', 'Joel'})
        self.assertEqual(self._names(hide_reapply='1'), {'Riya (again)', 'Joel'})

    def test_an_applicant_whose_other_application_is_elsewhere_still_shows(self):
        old = self._open('Godwin (old)', 'godwin@example.com')
        services.change_status(old, Candidate.Status.REJECTED)
        self._open('Godwin (new)', 'godwin@example.com')
        self.assertIn('Godwin (new)', self._names(hide_reapply='1'))


@override_settings(LOGIC_APP_CALENDAR_URL='')
class EditableCcTests(_HR):
    def setUp(self):
        super().setUp()
        self.candidate = Candidate.objects.create(full_name='Rose', email='rose@example.com', job=self.job)
        self.interview = Interview.objects.create(
            candidate=self.candidate, scheduled_date=timezone.now() + timedelta(days=1),
            mode=Interview.Mode.ONSITE, created_by=self.hr)
        self.url = reverse('interview_send_invite', args=[self.interview.pk])

    def test_cc_box_is_editable_and_prefilled(self):
        page = self.client.get(self.url)
        self.assertContains(page, 'name="cc_emails"')
        self.assertContains(page, 'careers@turnb.com')

    def test_hrs_cc_is_used(self):
        with mock.patch('interviews.invites.logic_app_mail.send_email') as send:
            self.client.post(self.url, {'to_email': 'rose@example.com', 'subject': 's', 'body': 'b',
                                        'cc_emails': 'lead@turnb.com; careers@turnb.com, rose@example.com'})
        self.assertEqual(send.call_args.kwargs['cc_emails'], ['lead@turnb.com', 'careers@turnb.com'])

    def test_a_bad_cc_address_is_refused(self):
        with mock.patch('interviews.invites.logic_app_mail.send_email') as send:
            response = self.client.post(self.url, {'to_email': 'rose@example.com', 'subject': 's', 'body': 'b',
                                                   'cc_emails': 'lead@turnb.com, not-an-email'}, **AJAX)
        send.assert_not_called()
        self.assertIn('not-an-email', response.json()['error'])


@override_settings(LOGIC_APP_CALENDAR_URL=CAL_URL)
class VideoInterviewMeetingTests(_HR):
    def setUp(self):
        super().setUp()
        self.candidate = Candidate.objects.create(full_name='Riya Paul', email='riya@example.com', job=self.job)
        self.when = timezone.localtime(timezone.now() + timedelta(days=1)).replace(
            hour=17, minute=30, second=0, microsecond=0)

    def _schedule(self, mode=Interview.Mode.VIDEO):
        reply = _reply(id='EVT-1', join_url='https://teams.microsoft.com/l/meetup-join/abc')
        with mock.patch('interviews.calendar_sync.requests.post', return_value=reply) as post:
            response = self.client.post(reverse('interview_schedule', args=[self.candidate.pk]), {
                'round_type': Interview.RoundType.ROUND1, 'interviewer': '',
                'scheduled_date': self.when.strftime('%Y-%m-%dT%H:%M'), 'mode': mode,
                'meeting_link': '', 'candidate_email': 'riya@example.com'}, **AJAX)
        return response, post

    def test_video_interview_without_interviewer_gets_a_teams_meeting(self):
        response, post = self._schedule()
        event = post.call_args.kwargs['json']['event']
        self.assertTrue(event['isOnlineMeeting'])
        self.assertEqual(event['attendees'], [])  # nobody to block yet
        interview = self.candidate.interviews.get()
        self.assertEqual(interview.meeting_link, 'https://teams.microsoft.com/l/meetup-join/abc')
        self.assertContains(response, 'Teams meeting invite')  # the draft says how it will go out
        self.assertContains(response, 'https://teams.microsoft.com/l/meetup-join/abc')  # link in the draft

    def test_onsite_without_interviewer_still_creates_nothing(self):
        _, post = self._schedule(mode=Interview.Mode.ONSITE)
        post.assert_not_called()

    def test_send_invite_adds_candidate_and_cc_to_the_meeting(self):
        self._schedule()
        interview = self.candidate.interviews.get()
        with mock.patch('interviews.calendar_sync.requests.post', return_value=_reply(id='EVT-1')) as post, \
             mock.patch('interviews.invites.logic_app_mail.send_email') as email:
            response = self.client.post(reverse('interview_send_invite', args=[interview.pk]), {
                'to_email': 'riya@example.com', 'subject': 'Interview Invitation - Program Manager',
                'body': 'Hello Riya,\nSee you then.', 'cc_emails': 'careers@turnb.com, lead@turnb.com'}, **AJAX)
        self.assertEqual(response.json(), {'ok': True})
        email.assert_not_called()  # the meeting IS the invite
        payload = post.call_args.kwargs['json']
        self.assertEqual((payload['op'], payload['event_id']), ('update', 'EVT-1'))
        event = payload['event']
        self.assertEqual(event['subject'], 'Interview Invitation - Program Manager')
        self.assertIn('Hello Riya,<br>See you then.', event['body']['content'])
        self.assertIn('https://teams.microsoft.com/l/meetup-join/abc', event['body']['content'])
        self.assertEqual([(a['emailAddress']['address'], a['type']) for a in event['attendees']], [
            ('riya@example.com', 'required'), ('careers@turnb.com', 'optional'), ('lead@turnb.com', 'optional')])
        self.assertTrue(Note.objects.filter(candidate=self.candidate,
                                            text__startswith='Interview invite sent as a Teams meeting').exists())

    def test_reschedule_after_the_invite_keeps_the_candidate_on_the_meeting(self):
        self._schedule()
        interview = self.candidate.interviews.get()
        with mock.patch('interviews.calendar_sync.requests.post', return_value=_reply(id='EVT-1')):
            self.client.post(reverse('interview_send_invite', args=[interview.pk]), {
                'to_email': 'riya@example.com', 'subject': 's', 'body': 'b', 'cc_emails': 'careers@turnb.com'})
        later = self.when + timedelta(hours=1)
        with mock.patch('interviews.calendar_sync.requests.post', return_value=_reply()) as post:
            self.client.post(reverse('interview_reschedule', args=[interview.pk]), {
                'round_type': Interview.RoundType.ROUND1, 'interviewer': '',
                'scheduled_date': later.strftime('%Y-%m-%dT%H:%M'), 'mode': Interview.Mode.VIDEO,
                'meeting_link': interview.meeting_link})
        event = post.call_args.kwargs['json']['event']
        self.assertNotIn('subject', event)  # HR's wording isn't overwritten
        self.assertEqual([a['emailAddress']['address'] for a in event['attendees']],
                         ['riya@example.com', 'careers@turnb.com'])
