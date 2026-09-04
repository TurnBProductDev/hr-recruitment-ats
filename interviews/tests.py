"""Tests for the interview scheduling rules.

Run against sqlite so the live Azure DB is never touched:
    DB_ENGINE=sqlite python manage.py test interviews
"""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from candidates.models import Candidate
from candidates.permissions import HR_ADMIN, INTERVIEWER
from jobs.models import Job

from .models import Interview, InterviewReschedule


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
            response, reverse('interview_schedule', args=[self.candidate.pk]))
        self.assertNotContains(
            response, reverse('interview_reschedule', args=[self.interview.pk]))

    def test_reschedule_link_is_offered_on_the_interviews_page(self):
        response = self.client.get(reverse('interview_scheduler'))
        self.assertContains(
            response, reverse('interview_reschedule', args=[self.interview.pk]))


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

    def test_cancelling_prompts_reject_or_hold(self):
        self.client.post(reverse('interview_cancel', args=[self.interview.pk]))
        self.interview.refresh_from_db()
        self.assertEqual(self.interview.status, Interview.Status.CANCELLED)
        response = self.client.get(reverse('candidate_timeline', args=[self.candidate.pk]))
        self.assertContains(response, 'Interview cancelled')
        # Still in the Schedule phase - a fresh interview can be scheduled instead.
        self.assertContains(response, reverse('interview_schedule', args=[self.candidate.pk]))

    def test_scheduling_a_new_interview_clears_the_cancelled_prompt(self):
        self.client.post(reverse('interview_cancel', args=[self.interview.pk]))
        Interview.objects.create(
            candidate=self.candidate, round_type=Interview.RoundType.ROUND1,
            scheduled_date=(timezone.now() + timezone.timedelta(days=2))
                           .replace(second=0, microsecond=0))
        response = self.client.get(reverse('candidate_timeline', args=[self.candidate.pk]))
        self.assertNotContains(response, 'Interview cancelled')
