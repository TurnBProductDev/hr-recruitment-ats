"""Tests for the interview scheduling rules.

Run against sqlite so the live Azure DB is never touched:
    DB_ENGINE=sqlite python manage.py test interviews
"""
from datetime import datetime
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from candidates.models import Candidate
from candidates.permissions import HR_ADMIN, INTERVIEWER, RECRUITER
from jobs.models import Job

from . import graph_client, invites, slot_emails
from .graph_client import GraphError
from .models import INTERVIEW_DURATION, Interview, InterviewReschedule, InterviewRequest


@override_settings(
    GRAPH_TENANT_ID='tenant-id', GRAPH_CLIENT_ID='client-id',
    GRAPH_CLIENT_SECRET='client-secret', GRAPH_ORGANIZER_EMAIL='careers@turnb.com')
class GraphClientTests(TestCase):
    """Unit tests for the Graph HTTP mechanics themselves - token fetch/cache
    and response parsing. GraphSchedulingIntegrationTests below covers how
    the rest of the app reacts to what this module reports."""

    def setUp(self):
        # The token cache is process-wide (LocMemCache) - clear it so one
        # test's cached token can't leak into the next.
        cache.clear()

    def _response(self, status_code=200, json_body=None, text=''):
        response = mock.Mock(status_code=status_code, text=text, content=b'{}')
        response.json.return_value = {} if json_body is None else json_body
        return response

    def test_is_configured_true_once_all_settings_are_set(self):
        self.assertTrue(graph_client.is_configured())

    @override_settings(GRAPH_CLIENT_SECRET='')
    def test_is_configured_false_if_any_setting_is_missing(self):
        self.assertFalse(graph_client.is_configured())

    def test_token_is_cached_across_calls(self):
        token_response = self._response(json_body={'access_token': 'abc123', 'expires_in': 3600})
        busy_response = self._response(json_body={'value': [{'availabilityView': '00'}]})
        start = timezone.now()
        with mock.patch('interviews.graph_client.requests.post', return_value=token_response) as post, \
             mock.patch('interviews.graph_client.requests.request', return_value=busy_response):
            graph_client.is_interviewer_busy('a@turnb.com', start, start + INTERVIEW_DURATION)
            graph_client.is_interviewer_busy('a@turnb.com', start, start + INTERVIEW_DURATION)
        post.assert_called_once()  # second call reused the cached token, no new token request

    def test_token_http_error_raises_graph_error(self):
        with mock.patch('interviews.graph_client.requests.post',
                        return_value=self._response(status_code=401, text='bad secret')):
            with self.assertRaises(GraphError):
                graph_client._get_token()

    def test_is_interviewer_busy_true_when_any_slot_is_busy(self):
        token_response = self._response(json_body={'access_token': 'abc123', 'expires_in': 3600})
        busy_response = self._response(json_body={'value': [{'availabilityView': '0010'}]})
        start = timezone.now()
        with mock.patch('interviews.graph_client.requests.post', return_value=token_response), \
             mock.patch('interviews.graph_client.requests.request', return_value=busy_response):
            self.assertTrue(graph_client.is_interviewer_busy('a@turnb.com', start, start + INTERVIEW_DURATION))

    def test_is_interviewer_busy_false_when_all_free(self):
        token_response = self._response(json_body={'access_token': 'abc123', 'expires_in': 3600})
        free_response = self._response(json_body={'value': [{'availabilityView': '0000'}]})
        start = timezone.now()
        with mock.patch('interviews.graph_client.requests.post', return_value=token_response), \
             mock.patch('interviews.graph_client.requests.request', return_value=free_response):
            self.assertFalse(graph_client.is_interviewer_busy('a@turnb.com', start, start + INTERVIEW_DURATION))

    def test_create_online_meeting_returns_join_url(self):
        token_response = self._response(json_body={'access_token': 'abc123', 'expires_in': 3600})
        meeting_response = self._response(json_body={'joinWebUrl': 'https://teams.microsoft.com/l/meetup/abc'})
        start = timezone.now()
        with mock.patch('interviews.graph_client.requests.post', return_value=token_response), \
             mock.patch('interviews.graph_client.requests.request', return_value=meeting_response):
            url = graph_client.create_online_meeting('Interview', start, start + INTERVIEW_DURATION)
        self.assertEqual(url, 'https://teams.microsoft.com/l/meetup/abc')

    def test_create_online_meeting_without_join_url_raises_graph_error(self):
        token_response = self._response(json_body={'access_token': 'abc123', 'expires_in': 3600})
        empty_response = self._response(json_body={})
        start = timezone.now()
        with mock.patch('interviews.graph_client.requests.post', return_value=token_response), \
             mock.patch('interviews.graph_client.requests.request', return_value=empty_response):
            with self.assertRaises(GraphError):
                graph_client.create_online_meeting('Interview', start, start + INTERVIEW_DURATION)


class OneOpenInterviewTests(TestCase):
    """A candidate may only have one interview awaiting a result at a time.
    A second one must not be schedulable - not even for a different role."""

    def setUp(self):
        self.user = get_user_model().objects.create_user('hr', 'hr@example.com', 'pw')
        self.user.groups.add(Group.objects.get_or_create(name=HR_ADMIN)[0])
        self.client.force_login(self.user)
        self.job = Job.objects.create(job_code='J1', title='Program Manager')
        self.other_job = Job.objects.create(job_code='J2', title='Analytics Consultant')
        self.candidate = Candidate.objects.create(
            full_name='Rose E G', email='rose@example.com', job=self.job)
        self.interview = Interview.objects.create(
            candidate=self.candidate, round_type=Interview.RoundType.ROUND1,
            scheduled_date=(timezone.now() + timezone.timedelta(days=1))
                           .replace(second=0, microsecond=0))

    def _schedule(self, **overrides):
        data = {
            'round_type': Interview.RoundType.ROUND1,
            'interviewer': '',
            'scheduled_date': '2026-09-01T10:00',
            'mode': Interview.Mode.VIDEO,
            'meeting_link': '',
        }
        data.update(overrides)
        return self.client.post(
            reverse('interview_schedule', args=[self.candidate.pk]), data)

    def test_schedule_form_is_refused_while_an_interview_awaits_a_result(self):
        response = self.client.get(
            reverse('interview_schedule', args=[self.candidate.pk]))
        self.assertRedirects(
            response, reverse('candidate_timeline', args=[self.candidate.pk]))

    def test_posting_a_second_interview_is_rejected(self):
        response = self._schedule()
        self.assertEqual(response.status_code, 200)  # redisplayed with the error
        self.assertContains(response, 'already has an open')
        self.assertEqual(self.candidate.interviews.count(), 1)

    def test_a_different_round_is_still_rejected(self):
        self._schedule(round_type=Interview.RoundType.TECHNICAL)
        self.assertEqual(self.candidate.interviews.count(), 1)

    def test_a_different_role_is_still_rejected(self):
        self.candidate.job = self.other_job
        self.candidate.save()
        self._schedule()
        self.assertEqual(self.candidate.interviews.count(), 1)

    def test_scheduling_works_once_the_result_is_marked(self):
        self.interview.status = Interview.Status.COMPLETED
        self.interview.result = Interview.Result.PASS_
        self.interview.save()
        self._schedule(round_type=Interview.RoundType.TECHNICAL)
        self.assertEqual(self.candidate.interviews.count(), 2)

    def test_a_cancelled_interview_does_not_block(self):
        self.interview.status = Interview.Status.CANCELLED
        self.interview.save()
        self._schedule()
        self.assertEqual(self.candidate.interviews.count(), 2)

    def test_another_candidate_is_unaffected(self):
        other = Candidate.objects.create(
            full_name='Nikhil Shaji', email='nikhil@example.com', job=self.job)
        self.client.post(reverse('interview_schedule', args=[other.pk]), {
            'round_type': Interview.RoundType.ROUND1, 'interviewer': '',
            'scheduled_date': '2026-09-01T10:00', 'mode': Interview.Mode.VIDEO,
            'meeting_link': ''})
        self.assertEqual(other.interviews.count(), 1)


class InterviewerConflictTests(TestCase):
    """The same interviewer cannot be double-booked into two overlapping
    interview slots scheduled through the ATS (see Interview.conflicts_for)."""

    def setUp(self):
        self.user = get_user_model().objects.create_user('hr', 'hr@example.com', 'pw')
        self.user.groups.add(Group.objects.get_or_create(name=HR_ADMIN)[0])
        self.client.force_login(self.user)
        self.job = Job.objects.create(job_code='J1', title='Program Manager')
        interviewer_group = Group.objects.get_or_create(name=INTERVIEWER)[0]
        self.interviewer = get_user_model().objects.create_user(
            'panel', 'panel@example.com', 'pw', first_name='Sreejith', last_name='K R')
        self.interviewer.groups.add(interviewer_group)
        self.other_interviewer = get_user_model().objects.create_user(
            'panel2', 'panel2@example.com', 'pw', first_name='Amrita', last_name='S')
        self.other_interviewer.groups.add(interviewer_group)
        self.candidate = Candidate.objects.create(
            full_name='Rose E G', email='rose@example.com', job=self.job)
        # Occupies 10:00-11:00 local time - matching how the form interprets
        # the naive datetime-local strings posted below.
        self.existing = Interview.objects.create(
            candidate=self.candidate, interviewer=self.interviewer,
            round_type=Interview.RoundType.ROUND1,
            scheduled_date=timezone.make_aware(datetime(2026, 9, 1, 10, 0)))
        self.other = Candidate.objects.create(
            full_name='Nikhil Shaji', email='nikhil@example.com', job=self.job)

    def _schedule(self, candidate, **overrides):
        data = {
            'round_type': Interview.RoundType.ROUND1,
            'interviewer': self.interviewer.pk,
            'scheduled_date': '2026-09-01T10:20',  # overlaps the 10:00-10:45 slot above
            'mode': Interview.Mode.VIDEO,
            'meeting_link': '',
        }
        data.update(overrides)
        return self.client.post(reverse('interview_schedule', args=[candidate.pk]), data)

    def test_overlapping_slot_for_same_interviewer_is_rejected(self):
        response = self._schedule(self.other)
        self.assertEqual(response.status_code, 200)  # redisplayed with the error
        self.assertContains(response, 'already interviewing')
        self.assertEqual(self.other.interviews.count(), 0)

    def test_non_overlapping_slot_is_accepted(self):
        self._schedule(self.other, scheduled_date='2026-09-01T11:00')
        self.assertEqual(self.other.interviews.count(), 1)

    def test_different_interviewer_at_the_same_time_is_unaffected(self):
        self._schedule(self.other, interviewer=self.other_interviewer.pk)
        self.assertEqual(self.other.interviews.count(), 1)

    def test_a_cancelled_interview_does_not_block(self):
        self.existing.status = Interview.Status.CANCELLED
        self.existing.save()
        self._schedule(self.other)
        self.assertEqual(self.other.interviews.count(), 1)

    def test_rescheduling_the_same_interview_does_not_conflict_with_itself(self):
        response = self.client.post(
            reverse('interview_reschedule', args=[self.existing.pk]),
            {'round_type': Interview.RoundType.ROUND1, 'interviewer': self.interviewer.pk,
             'scheduled_date': '2026-09-01T10:30', 'mode': Interview.Mode.VIDEO, 'meeting_link': ''})
        self.assertRedirects(
            response, reverse('candidate_timeline', args=[self.candidate.pk]))
        self.existing.refresh_from_db()
        self.assertEqual(
            timezone.localtime(self.existing.scheduled_date).strftime('%Y-%m-%d %H:%M'),
            '2026-09-01 10:30')


@override_settings(
    GRAPH_TENANT_ID='tenant-id', GRAPH_CLIENT_ID='client-id',
    GRAPH_CLIENT_SECRET='client-secret', GRAPH_ORGANIZER_EMAIL='careers@turnb.com')
class GraphSchedulingIntegrationTests(TestCase):
    """How InterviewForm/the schedule views react to what graph_client
    reports, with graph_client's own functions mocked directly - the HTTP
    mechanics themselves are GraphClientTests' job."""

    def setUp(self):
        self.user = get_user_model().objects.create_user('hr', 'hr@example.com', 'pw')
        self.user.groups.add(Group.objects.get_or_create(name=HR_ADMIN)[0])
        self.client.force_login(self.user)
        self.job = Job.objects.create(job_code='J1', title='Program Manager')
        self.interviewer = get_user_model().objects.create_user(
            'panel', 'panel@turnb.com', 'pw', first_name='Sreejith', last_name='K R')
        self.interviewer.groups.add(Group.objects.get_or_create(name=INTERVIEWER)[0])
        self.candidate = Candidate.objects.create(
            full_name='Rose E G', email='rose@example.com', job=self.job)

    def _schedule(self, **overrides):
        data = {
            'round_type': Interview.RoundType.ROUND1,
            'interviewer': self.interviewer.pk,
            'scheduled_date': '2026-09-01T10:00',
            'mode': Interview.Mode.VIDEO,
            'meeting_link': '',
        }
        data.update(overrides)
        return self.client.post(reverse('interview_schedule', args=[self.candidate.pk]), data)

    def test_busy_outlook_calendar_blocks_scheduling(self):
        with mock.patch('interviews.forms.graph_client.is_interviewer_busy', return_value=True):
            response = self._schedule()
        self.assertEqual(response.status_code, 200)  # redisplayed with the error
        self.assertContains(response, 'Outlook calendar')
        self.assertEqual(self.candidate.interviews.count(), 0)

    def test_free_outlook_calendar_allows_scheduling(self):
        with mock.patch('interviews.forms.graph_client.is_interviewer_busy', return_value=False), \
             mock.patch('interviews.views.graph_client.create_online_meeting',
                        return_value='https://teams.microsoft.com/l/meetup/xyz'):
            self._schedule()
        self.assertEqual(self.candidate.interviews.count(), 1)

    def test_a_graph_outage_does_not_block_scheduling(self):
        """A Graph failure must fail open - HR can still schedule using just
        the Phase 1 same-app check, not be stuck because Graph is down."""
        with mock.patch('interviews.forms.graph_client.is_interviewer_busy',
                        side_effect=GraphError('timed out')), \
             mock.patch('interviews.views.graph_client.create_online_meeting',
                        side_effect=GraphError('timed out')):
            self._schedule()
        self.assertEqual(self.candidate.interviews.count(), 1)

    def test_teams_link_is_auto_created_for_a_video_interview(self):
        with mock.patch('interviews.forms.graph_client.is_interviewer_busy', return_value=False), \
             mock.patch('interviews.views.graph_client.create_online_meeting',
                        return_value='https://teams.microsoft.com/l/meetup/xyz') as create:
            self._schedule()
        interview = self.candidate.interviews.get()
        self.assertEqual(interview.meeting_link, 'https://teams.microsoft.com/l/meetup/xyz')
        create.assert_called_once()

    def test_a_manually_entered_link_is_not_overwritten(self):
        with mock.patch('interviews.forms.graph_client.is_interviewer_busy', return_value=False), \
             mock.patch('interviews.views.graph_client.create_online_meeting') as create:
            self._schedule(meeting_link='https://teams.microsoft.com/l/meetup/manual')
        interview = self.candidate.interviews.get()
        self.assertEqual(interview.meeting_link, 'https://teams.microsoft.com/l/meetup/manual')
        create.assert_not_called()

    def test_no_teams_link_is_created_for_a_phone_interview(self):
        with mock.patch('interviews.forms.graph_client.is_interviewer_busy', return_value=False), \
             mock.patch('interviews.views.graph_client.create_online_meeting') as create:
            self._schedule(mode=Interview.Mode.PHONE)
        interview = self.candidate.interviews.get()
        self.assertFalse(interview.meeting_link)
        create.assert_not_called()

    def test_a_meeting_creation_failure_leaves_the_interview_scheduled(self):
        """Best-effort: Graph failing to create the meeting must not undo the
        interview that was already saved."""
        with mock.patch('interviews.forms.graph_client.is_interviewer_busy', return_value=False), \
             mock.patch('interviews.views.graph_client.create_online_meeting',
                        side_effect=GraphError('boom')):
            self._schedule()
        interview = self.candidate.interviews.get()
        self.assertFalse(interview.meeting_link)


class RescheduleTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            'hr', 'hr@example.com', 'pw', first_name='Dilshad', last_name='M N')
        self.user.groups.add(Group.objects.get_or_create(name=HR_ADMIN)[0])
        self.client.force_login(self.user)
        self.interviewer = get_user_model().objects.create_user(
            'sreejith', 'sreejith@example.com', 'pw', first_name='Sreejith', last_name='K R')
        self.interviewer.groups.add(Group.objects.get_or_create(name=INTERVIEWER)[0])
        self.job = Job.objects.create(job_code='J1', title='Program Manager')
        self.candidate = Candidate.objects.create(
            full_name='Rose E G', email='rose@example.com', job=self.job,
            status=Candidate.Status.ROUND1)
        self.interview = Interview.objects.create(
            candidate=self.candidate, round_type=Interview.RoundType.ROUND1,
            interviewer=self.interviewer,
            scheduled_date=(timezone.now() + timezone.timedelta(days=1))
                           .replace(second=0, microsecond=0))

    def _reschedule(self, **overrides):
        data = {
            'round_type': Interview.RoundType.ROUND1,
            'interviewer': self.interviewer.pk,
            'scheduled_date': '2026-09-05T11:30',
            'mode': Interview.Mode.VIDEO,
            'meeting_link': '',
        }
        data.update(overrides)
        return self.client.post(
            reverse('interview_reschedule', args=[self.interview.pk]), data)

    def test_rescheduling_rewrites_the_interview_row(self):
        """One interview, one row: the date is overwritten, not duplicated."""
        response = self._reschedule()
        self.assertRedirects(
            response, reverse('candidate_timeline', args=[self.candidate.pk]))
        self.interview.refresh_from_db()
        self.assertEqual(self.interview.status, Interview.Status.RESCHEDULED)
        self.assertEqual(
            timezone.localtime(self.interview.scheduled_date).strftime('%Y-%m-%d %H:%M'),
            '2026-09-05 11:30')
        self.assertEqual(self.candidate.interviews.count(), 1)

    def test_the_move_is_recorded_for_the_activity_history(self):
        before = self.interview.scheduled_date
        self._reschedule()
        log = InterviewReschedule.objects.get(interview=self.interview)
        self.assertEqual(log.previous_date, before)
        self.assertEqual(log.changed_by, self.user)
        self.assertIn('Date moved from', log.summary)

    def test_changing_only_the_interviewer_is_recorded_too(self):
        when = timezone.localtime(self.interview.scheduled_date).strftime('%Y-%m-%dT%H:%M')
        self._reschedule(scheduled_date=when, interviewer='')
        log = InterviewReschedule.objects.get(interview=self.interview)
        self.assertEqual(log.summary,
                         'Interviewer changed from Sreejith K R to Unassigned.')
        # The date did not move, so it is not a reschedule.
        self.interview.refresh_from_db()
        self.assertEqual(self.interview.status, Interview.Status.SCHEDULED)

    def test_saving_without_changes_records_nothing(self):
        when = timezone.localtime(self.interview.scheduled_date).strftime('%Y-%m-%dT%H:%M')
        self._reschedule(scheduled_date=when)
        self.assertFalse(InterviewReschedule.objects.exists())

    def test_the_reschedule_shows_up_in_the_activity_history(self):
        self._reschedule()
        response = self.client.get(
            reverse('candidate_timeline', args=[self.candidate.pk]))
        self.assertContains(response, 'Round 1 interview rescheduled')
        self.assertContains(response, 'Date moved from')

    def test_timeline_offers_reschedule_instead_of_schedule(self):
        response = self.client.get(
            reverse('candidate_timeline', args=[self.candidate.pk]))
        self.assertContains(
            response, reverse('interview_reschedule', args=[self.interview.pk]))
        self.assertNotContains(
            response, reverse('interview_schedule', args=[self.candidate.pk]))

    def test_timeline_offers_schedule_once_no_interview_is_open(self):
        self.interview.status = Interview.Status.COMPLETED
        self.interview.result = Interview.Result.PASS_
        self.interview.save()
        response = self.client.get(
            reverse('candidate_timeline', args=[self.candidate.pk]))
        self.assertContains(
            response, reverse('interview_allocate', args=[self.candidate.pk]))
        self.assertNotContains(
            response, reverse('interview_reschedule', args=[self.interview.pk]))

    def test_reschedule_link_is_offered_on_the_interviews_page(self):
        response = self.client.get(reverse('interview_scheduler'))
        self.assertContains(
            response, reverse('interview_reschedule', args=[self.interview.pk]))


class InterviewerRecommendationTests(TestCase):
    """An Interviewer's Pass/Fail (InterviewResultView) marks the interview
    Done but is only a recommendation - the candidate's stage only moves once
    HR/Recruiter/Hiring Manager decides, via the Hiring block's Cleared/Hold/
    Reject. HR/Recruiter/Hiring Manager submitting the same form directly
    still decides on the spot, as before."""

    def setUp(self):
        self.job = Job.objects.create(job_code='J1', title='Program Manager')
        self.candidate = Candidate.objects.create(
            full_name='Rose E G', email='rose@example.com', job=self.job,
            status=Candidate.Status.ROUND1)
        self.interview = Interview.objects.create(
            candidate=self.candidate, round_type=Interview.RoundType.ROUND1,
            scheduled_date=timezone.now() + timezone.timedelta(days=1))

    def _submit(self, user, **overrides):
        self.client.force_login(user)
        data = {'status': Interview.Status.COMPLETED, 'result': Interview.Result.PASS_,
                'score': '8', 'feedback': 'Strong candidate.'}
        data.update(overrides)
        return self.client.post(reverse('interview_result', args=[self.interview.pk]), data)

    def test_interviewer_pass_does_not_advance_the_candidate(self):
        interviewer = get_user_model().objects.create_user('panel', 'panel@turnb.com', 'pw')
        interviewer.groups.add(Group.objects.get_or_create(name=INTERVIEWER)[0])
        self.interview.interviewer = interviewer
        self.interview.save(update_fields=['interviewer'])
        self._submit(interviewer)
        self.interview.refresh_from_db()
        self.candidate.refresh_from_db()
        self.assertEqual(self.interview.status, Interview.Status.COMPLETED)
        self.assertEqual(self.interview.result, Interview.Result.PENDING)
        self.assertEqual(self.candidate.status, Candidate.Status.ROUND1)

    def test_interviewer_fail_does_not_reject_the_candidate(self):
        interviewer = get_user_model().objects.create_user('panel2', 'panel2@turnb.com', 'pw')
        interviewer.groups.add(Group.objects.get_or_create(name=INTERVIEWER)[0])
        self.interview.interviewer = interviewer
        self.interview.save(update_fields=['interviewer'])
        self._submit(interviewer, result=Interview.Result.FAIL)
        self.interview.refresh_from_db()
        self.candidate.refresh_from_db()
        self.assertEqual(self.interview.result, Interview.Result.PENDING)
        self.assertEqual(self.interview.feedback, 'Recommended: Fail. Strong candidate.')
        self.assertEqual(self.candidate.status, Candidate.Status.ROUND1)

    def test_interviewer_feedback_is_prefilled_on_the_candidate_page(self):
        interviewer = get_user_model().objects.create_user('panel3', 'panel3@turnb.com', 'pw')
        interviewer.groups.add(Group.objects.get_or_create(name=INTERVIEWER)[0])
        self.interview.interviewer = interviewer
        self.interview.save(update_fields=['interviewer'])
        self._submit(interviewer, feedback='Great communication skills.')
        # _submit force_logs-in as the interviewer, who can't reach
        # candidate_timeline (ANY_STAFF only) - switch to an HR viewer, who
        # is the one actually meant to see this pre-fill.
        hr = get_user_model().objects.create_user('hr4', 'hr4@turnb.com', 'pw')
        hr.groups.add(Group.objects.get_or_create(name=HR_ADMIN)[0])
        self.client.force_login(hr)
        response = self.client.get(reverse('candidate_timeline', args=[self.candidate.pk]))
        self.assertContains(response, 'Recommended: Pass. Great communication skills.')

    def test_hr_admin_pass_still_advances_the_candidate_immediately(self):
        hr = get_user_model().objects.create_user('hr3', 'hr3@turnb.com', 'pw')
        hr.groups.add(Group.objects.get_or_create(name=HR_ADMIN)[0])
        self._submit(hr)
        self.interview.refresh_from_db()
        self.candidate.refresh_from_db()
        self.assertEqual(self.interview.result, Interview.Result.PASS_)
        self.assertEqual(self.candidate.status, Candidate.Status.INTERVIEW)

    def test_interviewer_cannot_record_a_result_on_another_interviewers_interview(self):
        """IDOR fix: InterviewResultView.get_queryset() must scope a plain
        Interviewer to interviews actually assigned to them - otherwise
        changing the pk in the URL let one interviewer read and overwrite
        another's feedback/result."""
        owner = get_user_model().objects.create_user('owner', 'owner@turnb.com', 'pw')
        owner.groups.add(Group.objects.get_or_create(name=INTERVIEWER)[0])
        self.interview.interviewer = owner
        self.interview.save(update_fields=['interviewer'])

        outsider = get_user_model().objects.create_user('outsider', 'outsider@turnb.com', 'pw')
        outsider.groups.add(Group.objects.get_or_create(name=INTERVIEWER)[0])

        get_response = self._get_as(outsider)
        self.assertEqual(get_response.status_code, 404)

        response = self._submit(outsider, result=Interview.Result.FAIL, feedback='Sabotage.')
        self.assertEqual(response.status_code, 404)
        self.interview.refresh_from_db()
        self.assertIsNone(self.interview.feedback)
        self.assertEqual(self.interview.result, Interview.Result.PENDING)

    def _get_as(self, user):
        self.client.force_login(user)
        return self.client.get(reverse('interview_result', args=[self.interview.pk]))

    def test_recruiter_can_now_reach_this_view_and_decide(self):
        """Pre-existing gap: InterviewSchedulerListView (ANY_STAFF, includes
        Recruiter) links every row to this same "Update Status" URL, but
        InterviewResultView only allowed HR_ADMIN/INTERVIEWER - a Recruiter
        clicking that link got a 403. Now included, and decides on the spot
        same as HR."""
        recruiter = get_user_model().objects.create_user('rec2', 'rec2@turnb.com', 'pw')
        recruiter.groups.add(Group.objects.get_or_create(name=RECRUITER)[0])
        response = self._submit(recruiter, result=Interview.Result.FAIL)
        self.assertEqual(response.status_code, 302)
        self.candidate.refresh_from_db()
        self.assertEqual(self.candidate.status, Candidate.Status.REJECTED)

    def test_interviewer_hold_is_a_recommendation_only(self):
        interviewer = get_user_model().objects.create_user('panel3', 'panel3@turnb.com', 'pw')
        interviewer.groups.add(Group.objects.get_or_create(name=INTERVIEWER)[0])
        self.interview.interviewer = interviewer
        self.interview.save(update_fields=['interviewer'])
        self._submit(interviewer, result=Interview.Result.HOLD, feedback='Notice period too long.')
        self.interview.refresh_from_db()
        self.candidate.refresh_from_db()
        self.assertEqual(self.interview.status, Interview.Status.COMPLETED)
        self.assertEqual(self.interview.result, Interview.Result.PENDING)
        self.assertEqual(self.interview.feedback, 'Recommended: Hold. Notice period too long.')
        self.assertEqual(self.candidate.status, Candidate.Status.ROUND1)

    def test_hr_admin_hold_pauses_the_candidate_and_leaves_the_interview_pending(self):
        hr = get_user_model().objects.create_user('hr4', 'hr4@turnb.com', 'pw')
        hr.groups.add(Group.objects.get_or_create(name=HR_ADMIN)[0])
        self._submit(hr, result=Interview.Result.HOLD)
        self.interview.refresh_from_db()
        self.candidate.refresh_from_db()
        self.assertEqual(self.interview.status, Interview.Status.COMPLETED)
        # Hold pauses the candidate, it doesn't decide the interview - stays
        # Pending so the round's decision phase is still there on resume.
        self.assertEqual(self.interview.result, Interview.Result.PENDING)
        self.assertEqual(self.candidate.status, Candidate.Status.SCREENING_HOLD)
        self.assertEqual(self.candidate.hold_from_status, Candidate.Status.ROUND1)

    def test_result_is_required(self):
        hr = get_user_model().objects.create_user('hr5', 'hr5@turnb.com', 'pw')
        hr.groups.add(Group.objects.get_or_create(name=HR_ADMIN)[0])
        response = self._submit(hr, result='')
        self.assertEqual(response.status_code, 200)  # re-rendered with an error, not saved
        self.assertFormError(response.context['form'], 'result', 'This field is required.')
        self.interview.refresh_from_db()
        self.assertEqual(self.interview.status, Interview.Status.SCHEDULED)

    def test_pending_is_not_an_offered_result_choice(self):
        hr = get_user_model().objects.create_user('hr6', 'hr6@turnb.com', 'pw')
        hr.groups.add(Group.objects.get_or_create(name=HR_ADMIN)[0])
        self.client.force_login(hr)
        response = self.client.get(reverse('interview_result', args=[self.interview.pk]))
        self.assertNotContains(response, '"PENDING"')

    def test_feedback_is_required(self):
        hr = get_user_model().objects.create_user('hr7', 'hr7@turnb.com', 'pw')
        hr.groups.add(Group.objects.get_or_create(name=HR_ADMIN)[0])
        response = self._submit(hr, feedback='')
        self.assertEqual(response.status_code, 200)
        self.assertFormError(response.context['form'], 'feedback', 'This field is required.')
        self.interview.refresh_from_db()
        self.assertEqual(self.interview.status, Interview.Status.SCHEDULED)


class InterviewDoneCancelTests(TestCase):
    """Marking an interview "Done" only completes it - it doesn't decide
    pass/fail. That happens separately, when the Hiring block's Round 1/
    Round 2 decision card (Cleared/Hold/Reject) is used - see
    candidates.views.CandidateStatusActionView._settle_round_interview."""

    def setUp(self):
        self.user = get_user_model().objects.create_user('hr', 'hr@example.com', 'pw')
        self.user.groups.add(Group.objects.get_or_create(name=HR_ADMIN)[0])
        self.client.force_login(self.user)
        self.job = Job.objects.create(job_code='J1', title='Program Manager')
        self.candidate = Candidate.objects.create(
            full_name='Rose E G', email='rose@example.com', job=self.job,
            status=Candidate.Status.ROUND1)
        self.interview = Interview.objects.create(
            candidate=self.candidate, round_type=Interview.RoundType.ROUND1,
            scheduled_date=(timezone.now() + timezone.timedelta(days=1))
                           .replace(second=0, microsecond=0))

    def test_done_completes_without_deciding(self):
        self.client.post(reverse('interview_mark_done', args=[self.interview.pk]))
        self.interview.refresh_from_db()
        self.assertEqual(self.interview.status, Interview.Status.COMPLETED)
        self.assertEqual(self.interview.result, Interview.Result.PENDING)
        self.candidate.refresh_from_db()
        self.assertEqual(self.candidate.status, Candidate.Status.ROUND1)  # unchanged

    def test_timeline_shows_the_decision_card_once_done(self):
        self.client.post(reverse('interview_mark_done', args=[self.interview.pk]))
        response = self.client.get(reverse('candidate_timeline', args=[self.candidate.pk]))
        self.assertContains(response, 'Update Round 1 Status')
        self.assertContains(response, 'Cleared')

    def test_cleared_settles_the_interview_as_passed(self):
        self.client.post(reverse('interview_mark_done', args=[self.interview.pk]))
        self.client.post(reverse('candidate_interview_stage', args=[self.candidate.pk]))
        self.interview.refresh_from_db()
        self.assertEqual(self.interview.result, Interview.Result.PASS_)
        self.candidate.refresh_from_db()
        self.assertEqual(self.candidate.status, Candidate.Status.INTERVIEW)

    def test_reject_settles_the_interview_as_failed(self):
        self.client.post(reverse('interview_mark_done', args=[self.interview.pk]))
        self.client.post(reverse('candidate_reject', args=[self.candidate.pk]))
        self.interview.refresh_from_db()
        self.assertEqual(self.interview.result, Interview.Result.FAIL)

    def test_hold_leaves_the_interview_pending(self):
        self.client.post(reverse('interview_mark_done', args=[self.interview.pk]))
        self.client.post(reverse('candidate_screening_hold', args=[self.candidate.pk]))
        self.interview.refresh_from_db()
        self.assertEqual(self.interview.result, Interview.Result.PENDING)

    def test_reject_settles_a_still_scheduled_interview_without_mark_done_first(self):
        """HR deciding straight off the Hiring block, without ever clicking
        Done/Cancelled on the interview itself, must not leave it dangling
        Scheduled forever (see the interviewer portal's Result Pending tab)."""
        self.client.post(reverse('candidate_reject', args=[self.candidate.pk]))
        self.interview.refresh_from_db()
        self.assertEqual(self.interview.status, Interview.Status.COMPLETED)
        self.assertEqual(self.interview.result, Interview.Result.FAIL)

    def test_cancelling_prompts_reject_or_hold(self):
        self.client.post(reverse('interview_cancel', args=[self.interview.pk]))
        self.interview.refresh_from_db()
        self.assertEqual(self.interview.status, Interview.Status.CANCELLED)
        response = self.client.get(reverse('candidate_timeline', args=[self.candidate.pk]))
        self.assertContains(response, 'Interview cancelled')
        # Still in the Schedule phase - a fresh interviewer can be allocated instead.
        self.assertContains(response, reverse('interview_allocate', args=[self.candidate.pk]))

    def test_scheduling_a_new_interview_clears_the_cancelled_prompt(self):
        self.client.post(reverse('interview_cancel', args=[self.interview.pk]))
        Interview.objects.create(
            candidate=self.candidate, round_type=Interview.RoundType.ROUND1,
            scheduled_date=(timezone.now() + timezone.timedelta(days=2))
                           .replace(second=0, microsecond=0))
        response = self.client.get(reverse('candidate_timeline', args=[self.candidate.pk]))
        self.assertNotContains(response, 'Interview cancelled')


class InterviewerPortalTests(TestCase):
    """The restricted Interviewer portal: their own login, their own
    interviews list, a candidate view scoped to only candidates they're
    actually interviewing, and that the main HR app stays off-limits."""

    def setUp(self):
        self.interviewer = get_user_model().objects.create_user(
            'panel', 'panel@turnb.com', 'pw', first_name='Sreejith', last_name='K R')
        self.interviewer.groups.add(Group.objects.get_or_create(name=INTERVIEWER)[0])
        self.job = Job.objects.create(job_code='J1', title='Program Manager')
        self.candidate = Candidate.objects.create(
            full_name='Rose E G', email='rose@example.com', job=self.job)
        self.other_candidate = Candidate.objects.create(
            full_name='Nikhil Shaji', email='nikhil@example.com', job=self.job)
        self.interview = Interview.objects.create(
            candidate=self.candidate, interviewer=self.interviewer,
            round_type=Interview.RoundType.ROUND1,
            scheduled_date=(timezone.now() + timezone.timedelta(days=1))
                           .replace(second=0, microsecond=0))

    # ---- Interviewer login (separate from the HR sign-in) ----

    def test_interviewer_login_signs_them_in_and_lands_on_the_portal(self):
        response = self.client.post(reverse('interviewer_login'), {'username': 'panel', 'password': 'pw'})
        self.assertRedirects(response, reverse('interviewer_home'))

    def test_interviewer_login_refuses_a_non_interviewer_account(self):
        hr = get_user_model().objects.create_user('hr', 'hr@turnb.com', 'pw')
        hr.groups.add(Group.objects.get_or_create(name=RECRUITER)[0])
        response = self.client.post(reverse('interviewer_login'), {'username': 'hr', 'password': 'pw'})
        self.assertEqual(response.status_code, 200)  # redisplayed with the error
        self.assertContains(response, 'for interviewers only')
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_main_hr_login_still_works_for_interviewers_but_lands_on_the_portal(self):
        """Whichever door they used, an interviewer never lands on hr_dashboard
        (they have no access to it - see ANY_STAFF)."""
        response = self.client.post(reverse('login'), {'username': 'panel', 'password': 'pw'})
        self.assertRedirects(response, reverse('interviewer_home'))

    # ---- Portal home: only their own interviews ----

    def test_home_lists_only_their_own_interviews(self):
        other_interviewer = get_user_model().objects.create_user('other', 'other@turnb.com', 'pw')
        other_interviewer.groups.add(Group.objects.get_or_create(name=INTERVIEWER)[0])
        Interview.objects.create(
            candidate=self.other_candidate, interviewer=other_interviewer,
            round_type=Interview.RoundType.ROUND1,
            scheduled_date=timezone.now() + timezone.timedelta(days=1))
        self.client.force_login(self.interviewer)
        response = self.client.get(reverse('interviewer_home'))
        self.assertContains(response, 'Rose E G')
        self.assertNotContains(response, 'Nikhil Shaji')

    def test_completed_interviews_drop_off_the_portal_home(self):
        self.interview.status = Interview.Status.COMPLETED
        self.interview.result = Interview.Result.PASS_
        self.interview.save()
        self.client.force_login(self.interviewer)
        response = self.client.get(reverse('interviewer_home'))
        self.assertEqual(list(response.context['scheduled']), [])
        self.assertEqual(list(response.context['result_pending']), [])

    def test_scheduled_moves_to_result_pending_once_its_time_passes(self):
        self.interview.scheduled_date = timezone.now() - timezone.timedelta(hours=1)
        self.interview.save()
        self.client.force_login(self.interviewer)
        response = self.client.get(reverse('interviewer_home'))
        self.assertEqual(list(response.context['scheduled']), [])
        self.assertEqual(list(response.context['result_pending']), [self.interview])

    # ---- Candidate view: scoped to candidates they actually interview ----

    def test_can_view_a_candidate_they_are_interviewing(self):
        self.client.force_login(self.interviewer)
        response = self.client.get(reverse('interviewer_candidate', args=[self.candidate.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Rose E G')

    def test_cannot_view_an_unrelated_candidate(self):
        self.client.force_login(self.interviewer)
        response = self.client.get(reverse('interviewer_candidate', args=[self.other_candidate.pk]))
        self.assertEqual(response.status_code, 404)

    def test_cannot_open_the_cv_of_an_unrelated_candidate(self):
        self.other_candidate.resume_url = 'https://example.com/cv.pdf'
        self.other_candidate.save()
        self.client.force_login(self.interviewer)
        response = self.client.get(reverse('candidate_cv', args=[self.other_candidate.pk]))
        self.assertEqual(response.status_code, 404)

    # ---- The main HR app is off-limits ----

    def test_cannot_reach_the_hr_dashboard(self):
        self.client.force_login(self.interviewer)
        response = self.client.get(reverse('hr_dashboard'))
        self.assertEqual(response.status_code, 403)

    def test_cannot_reach_the_full_interview_scheduler(self):
        self.client.force_login(self.interviewer)
        response = self.client.get(reverse('interview_scheduler'))
        self.assertEqual(response.status_code, 403)

    def test_cannot_reach_the_candidate_repository(self):
        self.client.force_login(self.interviewer)
        response = self.client.get(reverse('candidate_repository'))
        self.assertEqual(response.status_code, 403)

    def test_cannot_reach_the_main_hr_candidate_timeline(self):
        self.client.force_login(self.interviewer)
        response = self.client.get(reverse('candidate_timeline', args=[self.candidate.pk]))
        self.assertEqual(response.status_code, 403)

    # ---- Recording a result routes back into the portal, not the HR app ----

    def test_recording_a_result_redirects_to_the_portal(self):
        self.client.force_login(self.interviewer)
        response = self.client.post(reverse('interview_result', args=[self.interview.pk]), {
            'status': Interview.Status.COMPLETED, 'result': Interview.Result.PASS_,
            'score': '8', 'feedback': 'Strong candidate.',
        })
        self.assertRedirects(response, reverse('interviewer_home'))
        self.interview.refresh_from_db()
        # An Interviewer's Pass/Fail is a recommendation, not the final call
        # (see InterviewResultView._can_decide_pipeline) - the interview is
        # marked Done, but its result stays Pending until HR/Recruiter
        # decides via the Hiring block; their pick and feedback are folded
        # together so HR sees both in one place.
        self.assertEqual(self.interview.status, Interview.Status.COMPLETED)
        self.assertEqual(self.interview.result, Interview.Result.PENDING)
        self.assertEqual(self.interview.feedback, 'Recommended: Pass. Strong candidate.')
        self.candidate.refresh_from_db()
        self.assertEqual(self.candidate.status, Candidate.Status.OPEN)


class ProposeSlotsSplitDateTimeTests(TestCase):
    """Each proposed slot is now a separate date input + time input
    (SplitDateTimeField/SplitDateTimeWidget) rather than one combined
    datetime-local field - posts as slot_N_0/slot_N_1, not a single slot_N."""

    def setUp(self):
        self.interviewer = get_user_model().objects.create_user(
            'panel', 'panel@turnb.com', 'pw', first_name='Sreejith', last_name='K R')
        self.interviewer.groups.add(Group.objects.get_or_create(name=INTERVIEWER)[0])
        self.client.force_login(self.interviewer)
        self.job = Job.objects.create(job_code='J1', title='Program Manager')
        self.candidate = Candidate.objects.create(
            full_name='Rose E G', email='rose@example.com', job=self.job)
        self.request_obj = InterviewRequest.objects.create(
            candidate=self.candidate, round_type=Interview.RoundType.ROUND1,
            interviewer=self.interviewer)

    def test_submitting_two_split_slots_creates_them_and_awaits_selection(self):
        response = self.client.post(
            reverse('interviewer_propose_slots', args=[self.request_obj.pk]), {
                'slot_1_0': '2026-09-20', 'slot_1_1': '10:00',
                'slot_2_0': '2026-09-20', 'slot_2_1': '14:00',
            })
        self.assertRedirects(response, reverse('interviewer_home'))
        self.request_obj.refresh_from_db()
        self.assertEqual(self.request_obj.status, InterviewRequest.Status.AWAITING_SELECTION)
        self.assertEqual(self.request_obj.slots.count(), 2)

    def test_an_incomplete_slot_is_rejected(self):
        response = self.client.post(
            reverse('interviewer_propose_slots', args=[self.request_obj.pk]), {
                'slot_1_0': '2026-09-20', 'slot_1_1': '',
                'slot_2_0': '2026-09-20', 'slot_2_1': '14:00',
            })
        self.assertEqual(response.status_code, 400)  # redisplayed with the error
        self.assertEqual(self.request_obj.slots.count(), 0)


class InterviewerPortalAdminTests(TestCase):
    """Admin sees every interview/candidate in the portal, not just their
    own - unlike a plain Interviewer, who stays scoped to themselves."""

    def setUp(self):
        self.admin = get_user_model().objects.create_user('admin', 'admin@turnb.com', 'pw')
        self.admin.groups.add(Group.objects.get_or_create(name=HR_ADMIN)[0])
        self.interviewer_a = get_user_model().objects.create_user(
            'panel_a', 'panel_a@turnb.com', 'pw', first_name='Sreejith', last_name='K R')
        self.interviewer_a.groups.add(Group.objects.get_or_create(name=INTERVIEWER)[0])
        self.interviewer_b = get_user_model().objects.create_user(
            'panel_b', 'panel_b@turnb.com', 'pw', first_name='Amrita', last_name='S')
        self.interviewer_b.groups.add(Group.objects.get_or_create(name=INTERVIEWER)[0])
        self.job = Job.objects.create(job_code='J1', title='Program Manager')
        self.candidate_a = Candidate.objects.create(
            full_name='Rose E G', email='rose@example.com', job=self.job)
        self.candidate_b = Candidate.objects.create(
            full_name='Nikhil Shaji', email='nikhil@example.com', job=self.job)
        self.interview_a = Interview.objects.create(
            candidate=self.candidate_a, interviewer=self.interviewer_a,
            round_type=Interview.RoundType.ROUND1,
            scheduled_date=timezone.now() + timezone.timedelta(days=1))
        self.interview_b = Interview.objects.create(
            candidate=self.candidate_b, interviewer=self.interviewer_b,
            round_type=Interview.RoundType.ROUND1,
            scheduled_date=timezone.now() + timezone.timedelta(days=2))

    def test_admin_can_sign_in_through_the_interviewer_login(self):
        response = self.client.post(reverse('interviewer_login'), {'username': 'admin', 'password': 'pw'})
        self.assertRedirects(response, reverse('interviewer_home'))

    def test_admin_sees_every_interviewers_interviews(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse('interviewer_home'))
        self.assertContains(response, 'Rose E G')
        self.assertContains(response, 'Nikhil Shaji')

    def test_plain_interviewer_still_only_sees_their_own(self):
        self.client.force_login(self.interviewer_a)
        response = self.client.get(reverse('interviewer_home'))
        self.assertContains(response, 'Rose E G')
        self.assertNotContains(response, 'Nikhil Shaji')

    def test_admin_can_view_any_candidate_with_an_interview(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse('interviewer_candidate', args=[self.candidate_b.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Nikhil Shaji')

    def test_a_recruiter_still_cannot_reach_the_portal(self):
        recruiter = get_user_model().objects.create_user('rec', 'rec@turnb.com', 'pw')
        recruiter.groups.add(Group.objects.get_or_create(name=RECRUITER)[0])
        self.client.force_login(recruiter)
        response = self.client.get(reverse('interviewer_home'))
        self.assertEqual(response.status_code, 403)


class LogoutRedirectTests(TestCase):
    """Logging out used to land on the public careers page
    (LOGOUT_REDIRECT_URL='vacancy_list') - now it's a sign-in page instead,
    the right one for whichever role just logged out."""

    def test_hr_user_lands_on_the_main_login(self):
        hr = get_user_model().objects.create_user('hr', 'hr@turnb.com', 'pw')
        hr.groups.add(Group.objects.get_or_create(name=HR_ADMIN)[0])
        self.client.force_login(hr)
        response = self.client.post(reverse('logout'))
        self.assertRedirects(response, reverse('login'))

    def test_interviewer_lands_on_the_interviewer_login(self):
        interviewer = get_user_model().objects.create_user('panel', 'panel@turnb.com', 'pw')
        interviewer.groups.add(Group.objects.get_or_create(name=INTERVIEWER)[0])
        self.client.force_login(interviewer)
        response = self.client.post(reverse('logout'))
        self.assertRedirects(response, reverse('interviewer_login'))


class AdminPortalSessionModeTests(TestCase):
    """An Admin who signs in via the Interviewer login gets the same
    restricted nav/back-links/breadcrumbs/logout-target an Interviewer does,
    for as long as that session lasts - "which door you came in through"
    (candidates.permissions.in_interviewer_portal), not just role. Logging
    in via force_login() bypasses InterviewerLoginView.form_valid() entirely,
    so every test here signs in for real via a POST to reach it."""

    def setUp(self):
        self.admin = get_user_model().objects.create_user('admin2', 'admin2@turnb.com', 'pw')
        self.admin.groups.add(Group.objects.get_or_create(name=HR_ADMIN)[0])
        self.job = Job.objects.create(job_code='J1', title='Program Manager')
        self.candidate = Candidate.objects.create(
            full_name='Rose E G', email='rose@example.com', job=self.job)
        self.interview = Interview.objects.create(
            candidate=self.candidate, round_type=Interview.RoundType.ROUND1,
            scheduled_date=timezone.now() + timezone.timedelta(days=1))

    def _login_via_portal(self):
        self.client.post(reverse('interviewer_login'), {'username': 'admin2', 'password': 'pw'})

    def test_nav_is_restricted_after_portal_login(self):
        self._login_via_portal()
        response = self.client.get(reverse('interviewer_home'))
        self.assertContains(response, 'My Interviews')
        self.assertNotContains(response, 'Vacancies')

    def test_record_result_cancel_points_back_into_the_portal_not_hr(self):
        self._login_via_portal()
        response = self.client.get(reverse('interview_result', args=[self.interview.pk]))
        self.assertContains(response, reverse('interviewer_candidate', args=[self.candidate.pk]))
        self.assertNotContains(response, reverse('candidate_timeline', args=[self.candidate.pk]))

    def test_recording_a_result_redirects_back_to_the_portal(self):
        self._login_via_portal()
        response = self.client.post(reverse('interview_result', args=[self.interview.pk]), {
            'status': Interview.Status.COMPLETED, 'result': Interview.Result.PASS_,
            'score': '8', 'feedback': 'Good.',
        })
        self.assertRedirects(response, reverse('interviewer_home'))

    def test_logout_returns_to_the_interviewer_login(self):
        self._login_via_portal()
        response = self.client.post(reverse('logout'))
        self.assertRedirects(response, reverse('interviewer_login'))

    def test_logging_in_via_the_main_hr_login_clears_portal_mode(self):
        self._login_via_portal()
        self.client.post(reverse('logout'))
        self.client.post(reverse('login'), {'username': 'admin2', 'password': 'pw'})
        response = self.client.get(reverse('hr_dashboard'))
        self.assertContains(response, 'Vacancies')
        self.assertNotContains(response, 'My Interviews')

    def test_normal_hr_login_never_enters_portal_mode(self):
        self.client.post(reverse('login'), {'username': 'admin2', 'password': 'pw'})
        response = self.client.get(reverse('interview_result', args=[self.interview.pk]))
        self.assertContains(response, reverse('candidate_timeline', args=[self.candidate.pk]))


@override_settings(LOGIC_APP_EMAIL_SENDER_URL='https://logic.example/send-email')
class InviteAndSlotEmailTests(TestCase):
    """interviews/invites.py and interviews/slot_emails.py both send through
    the Send-Email-Notifier Logic App (candidates/logic_app_mail.py), not
    Django's own mail backend - same as candidates/rejection_emails.py."""

    def setUp(self):
        self.job = Job.objects.create(job_code='J1', title='Program Manager')
        self.interviewer = get_user_model().objects.create_user(
            'panel', 'panel@turnb.com', 'pw', first_name='Sreejith', last_name='K R')
        self.interviewer.groups.add(Group.objects.get_or_create(name=INTERVIEWER)[0])
        self.candidate = Candidate.objects.create(
            full_name='Rose E G', email='rose@example.com', job=self.job)
        self.interview = Interview.objects.create(
            candidate=self.candidate, interviewer=self.interviewer,
            round_type=Interview.RoundType.ROUND1,
            scheduled_date=timezone.now() + timezone.timedelta(days=1))

    def _ok_response(self):
        return mock.Mock(status_code=200, text='')

    def test_send_invite_posts_an_ics_attachment(self):
        with mock.patch('candidates.logic_app_mail.requests.post',
                        return_value=self._ok_response()) as post:
            invites.send_invite(
                self.interview, to_email='rose@example.com', cc_emails=['careers@turnb.com'],
                subject='Interview Invite', body='See you then.', sender=self.interviewer)
        payload = post.call_args.kwargs['json']
        self.assertEqual(payload['to'], 'rose@example.com')
        self.assertEqual(payload['subject'], 'Interview Invite')
        self.assertEqual(len(payload['attachments']), 1)
        self.assertEqual(payload['attachments'][0]['Name'], 'interview-invite.ics')

    def test_notify_interviewer_new_request(self):
        request_obj = InterviewRequest.objects.create(
            candidate=self.candidate, round_type=Interview.RoundType.ROUND1,
            interviewer=self.interviewer)
        with mock.patch('candidates.logic_app_mail.requests.post',
                        return_value=self._ok_response()) as post:
            slot_emails.notify_interviewer_new_request(request_obj)
        payload = post.call_args.kwargs['json']
        self.assertEqual(payload['to'], self.interviewer.email)
        self.assertIn(self.candidate.full_name, payload['body'])
