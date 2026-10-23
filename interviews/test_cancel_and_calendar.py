"""Cancelling an interview at any stage, showing the proposed slots on the
candidate page, and blocking the interviewer's calendar once the candidate
picks a slot (interviews/calendar_sync.py).

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
from candidates.models import Candidate
from candidates.permissions import HR_ADMIN, INTERVIEWER
from jobs.models import Job

from .models import Interview, InterviewRequest

CAL_URL = 'https://calendar.example/trigger'


def _calendar_reply(**data):
    response = mock.Mock(status_code=200, content=b'{}')
    response.json.return_value = data
    return response


@override_settings(LOGIC_APP_CALENDAR_URL=CAL_URL)
class _Base(TestCase):
    def setUp(self):
        self.hr = get_user_model().objects.create_user('hr1', 'hr1@turnb.com', 'pw')
        self.hr.groups.add(Group.objects.get_or_create(name=HR_ADMIN)[0])
        self.client.force_login(self.hr)
        self.interviewer = get_user_model().objects.create_user(
            'panel', 'panel@turnb.com', 'pw', first_name='Sreejith', last_name='K R')
        self.interviewer.groups.add(Group.objects.get_or_create(name=INTERVIEWER)[0])
        self.job = Job.objects.create(job_code='J1', title='Program Manager')
        self.candidate = Candidate.objects.create(full_name='Rose E G', email='rose@example.com', job=self.job)
        services.change_status(self.candidate, Candidate.Status.ROUND1)
        self.start = (timezone.now() + timedelta(days=2)).replace(second=0, microsecond=0)

    def _request(self, status, **extra):
        return InterviewRequest.objects.create(
            candidate=self.candidate, round_type=Interview.RoundType.ROUND1, interviewer=self.interviewer,
            status=status, created_by=self.hr, **extra)

    def _picked_request(self, **extra):
        request_obj = self._request(InterviewRequest.Status.AWAITING_HR_APPROVAL, **extra)
        slot = request_obj.slots.create(start_datetime=self.start)
        request_obj.candidate_selected_slot = slot
        request_obj.save(update_fields=['candidate_selected_slot'])
        return request_obj


class CancelRequestTests(_Base):
    """Feature 1 - cancel before the interviewer proposes slots, while the
    candidate is picking, or while HR's approval is pending."""

    def test_cancel_before_slots_are_proposed(self):
        request_obj = self._request(InterviewRequest.Status.AWAITING_SLOTS)
        with mock.patch('interviews.slot_emails.logic_app_mail.send_email') as send:
            response = self.client.post(reverse('interview_request_cancel', args=[request_obj.pk]))
        self.assertRedirects(response, reverse('candidate_timeline', args=[self.candidate.pk]))
        request_obj.refresh_from_db()
        self.assertEqual(request_obj.status, InterviewRequest.Status.CANCELLED)
        self.assertEqual(send.call_args.kwargs['to_email'], 'panel@turnb.com')
        self.assertIn('Interview cancelled - Rose E G', send.call_args.kwargs['subject'])
        self.assertTrue(self.interviewer.notifications.filter(title__startswith='Interview cancelled').exists())

    def test_cancel_while_candidate_is_picking_kills_the_pick_link(self):
        request_obj = self._request(InterviewRequest.Status.AWAITING_SELECTION, slots_proposed_at=timezone.now())
        request_obj.slots.create(start_datetime=self.start)
        token = request_obj.new_candidate_token()
        request_obj.save(update_fields=['candidate_token'])
        with mock.patch('interviews.slot_emails.logic_app_mail.send_email'):
            self.client.post(reverse('interview_request_cancel', args=[request_obj.pk]))
        request_obj.refresh_from_db()
        self.assertIsNone(request_obj.candidate_token)
        self.assertEqual(self.client.get(reverse('candidate_slot_pick', args=[token])).status_code, 404)

    def test_cancel_after_the_pick_withdraws_the_calendar_block(self):
        request_obj = self._picked_request(calendar_event_id='EVT-1', calendar_join_url='https://teams.example/j')
        with mock.patch('interviews.calendar_sync.requests.post', return_value=_calendar_reply()) as post, \
             mock.patch('interviews.slot_emails.logic_app_mail.send_email') as send:
            self.client.post(reverse('interview_request_cancel', args=[request_obj.pk]))
        payload = post.call_args.kwargs['json']
        self.assertEqual((payload['op'], payload['event_id']), ('cancel', 'EVT-1'))
        request_obj.refresh_from_db()
        self.assertEqual((request_obj.status, request_obj.calendar_event_id),
                         (InterviewRequest.Status.CANCELLED, ''))
        self.assertIn(' IST', send.call_args.kwargs['body'])  # the picked time is named

    def test_card_offers_allocate_again_after_cancel(self):
        request_obj = self._request(InterviewRequest.Status.AWAITING_SLOTS)
        with mock.patch('interviews.slot_emails.logic_app_mail.send_email'):
            self.client.post(reverse('interview_request_cancel', args=[request_obj.pk]))
        page = self.client.get(reverse('candidate_timeline', args=[self.candidate.pk]))
        self.assertContains(page, 'Allocate Interviewer')
        self.assertNotContains(page, 'Cancel Interview')

    def test_a_scheduled_request_cannot_be_cancelled_here(self):
        request_obj = self._request(InterviewRequest.Status.SCHEDULED)
        response = self.client.post(reverse('interview_request_cancel', args=[request_obj.pk]))
        self.assertEqual(response.status_code, 404)


class CancelScheduledInterviewTests(_Base):
    def _interview(self, when, **extra):
        return Interview.objects.create(candidate=self.candidate, round_type=Interview.RoundType.ROUND1,
                                        interviewer=self.interviewer, scheduled_date=when, **extra)

    def test_upcoming_interview_cancel_tells_the_interviewer_and_frees_the_calendar(self):
        interview = self._interview(self.start, calendar_event_id='EVT-2')
        with mock.patch('interviews.calendar_sync.requests.post', return_value=_calendar_reply()) as post, \
             mock.patch('interviews.slot_emails.logic_app_mail.send_email') as send:
            self.client.post(reverse('interview_cancel', args=[interview.pk]))
        self.assertEqual(post.call_args.kwargs['json']['op'], 'cancel')
        send.assert_called_once()
        interview.refresh_from_db()
        self.assertEqual((interview.status, interview.calendar_event_id), (Interview.Status.CANCELLED, ''))

    def test_past_interview_cancel_is_just_a_no_show(self):
        interview = self._interview(timezone.now() - timedelta(hours=2), calendar_event_id='EVT-3')
        with mock.patch('interviews.calendar_sync.requests.post') as post, \
             mock.patch('interviews.slot_emails.logic_app_mail.send_email') as send:
            self.client.post(reverse('interview_cancel', args=[interview.pk]))
        post.assert_not_called()
        send.assert_not_called()


class ProposedSlotsOnCandidatePageTests(_Base):
    """Feature 2 - the slots are listed before the candidate picks; the
    picked date afterwards."""

    def test_proposed_slots_are_listed(self):
        request_obj = self._request(InterviewRequest.Status.AWAITING_SELECTION, slots_proposed_at=timezone.now())
        second = self.start + timedelta(days=1, hours=2)
        request_obj.slots.create(start_datetime=self.start)
        request_obj.slots.create(start_datetime=second)
        page = self.client.get(reverse('candidate_timeline', args=[self.candidate.pk]))
        for when in (self.start, second):
            self.assertContains(page, timezone.localtime(when).strftime('%d %b %Y, %H:%M'))
        self.assertContains(page, 'proposed 2 slots')

    def test_selected_date_is_shown_after_the_pick(self):
        self._picked_request(calendar_event_id='EVT-4')
        page = self.client.get(reverse('candidate_timeline', args=[self.candidate.pk]))
        self.assertContains(page, 'Interview date selected by Rose E G')
        self.assertContains(page, timezone.localtime(self.start).strftime('%d %b %Y, %H:%M'))
        self.assertContains(page, 'Blocked on Sreejith K R')


class CalendarBlockTests(_Base):
    """Feature 3 - the candidate's pick blocks the interviewer's calendar."""

    def _awaiting_pick(self, mode=Interview.Mode.VIDEO):
        request_obj = self._request(InterviewRequest.Status.AWAITING_SELECTION, mode=mode,
                                    slots_proposed_at=timezone.now())
        slot = request_obj.slots.create(start_datetime=self.start)
        request_obj.new_candidate_token()
        request_obj.save(update_fields=['candidate_token'])
        return request_obj, slot

    def test_candidate_pick_creates_a_teams_meeting_with_the_interviewer(self):
        request_obj, slot = self._awaiting_pick()
        self.client.logout()  # the pick page is public
        reply = _calendar_reply(id='EVT-5', join_url='https://teams.microsoft.com/l/meetup-join/x')
        with mock.patch('interviews.calendar_sync.requests.post', return_value=reply) as post:
            self.client.post(reverse('candidate_slot_pick', args=[request_obj.candidate_token]), {'slot': slot.pk})
        payload = post.call_args.kwargs['json']
        event = payload['event']
        self.assertEqual(payload['op'], 'create')
        self.assertEqual(event['attendees'][0]['emailAddress']['address'], 'panel@turnb.com')
        self.assertTrue(event['isOnlineMeeting'])
        self.assertEqual(event['start']['timeZone'], 'UTC')
        request_obj.refresh_from_db()
        self.assertEqual(request_obj.status, InterviewRequest.Status.AWAITING_HR_APPROVAL)
        self.assertEqual(request_obj.calendar_event_id, 'EVT-5')

    def test_onsite_interview_blocks_time_without_a_teams_link(self):
        request_obj, slot = self._awaiting_pick(mode=Interview.Mode.ONSITE)
        with mock.patch('interviews.calendar_sync.requests.post', return_value=_calendar_reply(id='EVT-6')) as post:
            self.client.post(reverse('candidate_slot_pick', args=[request_obj.candidate_token]), {'slot': slot.pk})
        self.assertNotIn('isOnlineMeeting', post.call_args.kwargs['json']['event'])

    def test_calendar_outage_never_blocks_the_pick(self):
        request_obj, slot = self._awaiting_pick()
        failed = mock.Mock(status_code=502, text='bad gateway')
        with mock.patch('interviews.calendar_sync.requests.post', return_value=failed):
            self.client.post(reverse('candidate_slot_pick', args=[request_obj.candidate_token]), {'slot': slot.pk})
        request_obj.refresh_from_db()
        self.assertEqual(request_obj.status, InterviewRequest.Status.AWAITING_HR_APPROVAL)
        self.assertEqual(request_obj.calendar_event_id, '')

    @override_settings(LOGIC_APP_CALENDAR_URL='')
    def test_inert_without_the_logic_app_url(self):
        request_obj, slot = self._awaiting_pick()
        with mock.patch('interviews.calendar_sync.requests.post') as post:
            self.client.post(reverse('candidate_slot_pick', args=[request_obj.candidate_token]), {'slot': slot.pk})
        post.assert_not_called()

    def test_approval_reuses_the_blocked_meeting_and_its_teams_link(self):
        request_obj = self._picked_request(calendar_event_id='EVT-7',
                                           calendar_join_url='https://teams.microsoft.com/l/meetup-join/y')
        with mock.patch('interviews.calendar_sync.requests.post') as post:
            self.client.post(reverse('interview_request_approve', args=[request_obj.pk]))
        post.assert_not_called()  # nothing new created
        interview = self.candidate.interviews.get()
        self.assertEqual(interview.calendar_event_id, 'EVT-7')
        self.assertEqual(interview.meeting_link, 'https://teams.microsoft.com/l/meetup-join/y')

    def test_new_slots_request_withdraws_the_block(self):
        request_obj = self._picked_request(calendar_event_id='EVT-8')
        with mock.patch('interviews.calendar_sync.requests.post', return_value=_calendar_reply()) as post, \
             mock.patch('interviews.slot_emails.logic_app_mail.send_email'):
            self.client.post(reverse('interview_request_new_slots', args=[request_obj.pk]))
        self.assertEqual(post.call_args.kwargs['json'], {
            'op': 'cancel', 'event_id': 'EVT-8', 'comment': 'HR asked for a different time.'})
        request_obj.refresh_from_db()
        self.assertEqual(request_obj.calendar_event_id, '')

    def test_reschedule_moves_the_meeting(self):
        interview = Interview.objects.create(
            candidate=self.candidate, round_type=Interview.RoundType.ROUND1, interviewer=self.interviewer,
            scheduled_date=self.start, calendar_event_id='EVT-9')
        new_time = timezone.localtime(self.start + timedelta(days=1))
        with mock.patch('interviews.calendar_sync.requests.post', return_value=_calendar_reply()) as post:
            self.client.post(reverse('interview_reschedule', args=[interview.pk]), {
                'round_type': Interview.RoundType.ROUND1, 'interviewer': self.interviewer.pk,
                'scheduled_date': new_time.strftime('%Y-%m-%dT%H:%M'), 'mode': Interview.Mode.VIDEO,
                'meeting_link': ''})
        payload = post.call_args.kwargs['json']
        self.assertEqual((payload['op'], payload['event_id']), ('update', 'EVT-9'))
