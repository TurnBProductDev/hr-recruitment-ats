"""Optional second interviewer (Interviewer 2) on Round 1/2 allocations.

    DB_ENGINE=sqlite python manage.py test interviews.test_co_interviewer
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

from .forms import InterviewAllocationForm, InterviewSlotProposalForm
from .models import Interview, InterviewRequest

CAL_URL = 'https://calendar.example/trigger'


def _calendar_reply(**data):
    response = mock.Mock(status_code=200, content=b'{}')
    response.json.return_value = data
    return response


@override_settings(LOGIC_APP_CALENDAR_URL=CAL_URL)
class CoInterviewerTests(TestCase):
    def setUp(self):
        User = get_user_model()
        group = Group.objects.get_or_create(name=INTERVIEWER)[0]
        self.hr = User.objects.create_user('hr1', 'hr1@turnb.com', 'pw')
        self.hr.groups.add(Group.objects.get_or_create(name=HR_ADMIN)[0])
        self.lead = User.objects.create_user('lead', 'lead@turnb.com', 'pw', first_name='Sreejith')
        self.co = User.objects.create_user('co', 'co@turnb.com', 'pw', first_name='Anu')
        self.lead.groups.add(group)
        self.co.groups.add(group)
        self.job = Job.objects.create(job_code='J1', title='Program Manager')
        self.candidate = Candidate.objects.create(full_name='Rose E G', email='rose@example.com', job=self.job,
                                                  resume_url='https://example.com/cv.pdf')
        services.change_status(self.candidate, Candidate.Status.ROUND1)
        self.start = (timezone.now() + timedelta(days=2)).replace(second=0, microsecond=0)
        self.client.force_login(self.hr)

    def _request(self, status=InterviewRequest.Status.AWAITING_SLOTS, **extra):
        return InterviewRequest.objects.create(
            candidate=self.candidate, round_type=Interview.RoundType.ROUND1, interviewer=self.lead,
            co_interviewer=self.co, status=status, created_by=self.hr, **extra)

    # ---- allocation ----

    def test_allocate_with_interviewer_2_emails_them_a_heads_up(self):
        with mock.patch('interviews.slot_emails.logic_app_mail.send_email') as send:
            self.client.post(reverse('interview_allocate', args=[self.candidate.pk]), {
                'round_type': 'ROUND1', 'interviewer': self.lead.pk, 'co_interviewer': self.co.pk,
                'mode': 'VIDEO'})
        request_obj = InterviewRequest.objects.get(candidate=self.candidate)
        self.assertEqual(request_obj.co_interviewer, self.co)
        recipients = [c.kwargs['to_email'] for c in send.call_args_list]
        self.assertEqual(recipients, ['lead@turnb.com', 'co@turnb.com'])
        self.assertIn('Interviewer 2', send.call_args_list[1].kwargs['subject'])
        self.assertTrue(self.co.notifications.filter(title__startswith='Interviewer 2').exists())

    def test_interviewer_2_is_optional(self):
        form = InterviewAllocationForm(
            {'round_type': 'ROUND1', 'interviewer': self.lead.pk, 'mode': 'VIDEO'}, candidate=self.candidate)
        self.assertTrue(form.is_valid(), form.errors)

    def test_interviewer_2_must_differ_from_interviewer_1(self):
        form = InterviewAllocationForm(
            {'round_type': 'ROUND1', 'interviewer': self.lead.pk, 'co_interviewer': self.lead.pk,
             'mode': 'VIDEO'}, candidate=self.candidate)
        self.assertFalse(form.is_valid())
        self.assertIn('co_interviewer', form.errors)

    # ---- candidate picks a slot: both on the meeting ----

    def test_candidate_pick_invites_both_interviewers_to_the_meeting(self):
        request_obj = self._request(InterviewRequest.Status.AWAITING_SELECTION, slots_proposed_at=timezone.now())
        slot = request_obj.slots.create(start_datetime=self.start)
        request_obj.new_candidate_token()
        request_obj.save(update_fields=['candidate_token'])
        self.client.logout()
        with mock.patch('interviews.calendar_sync.requests.post',
                        return_value=_calendar_reply(id='EVT-1', join_url='https://teams/x')) as post, \
                mock.patch('interviews.slot_emails.logic_app_mail.send_email'):
            self.client.post(reverse('candidate_slot_pick', args=[request_obj.candidate_token]), {'slot': slot.pk})
        attendees = post.call_args.kwargs['json']['event']['attendees']
        self.assertEqual([(a['emailAddress']['address'], a['type']) for a in attendees],
                         [('lead@turnb.com', 'required'), ('co@turnb.com', 'required')])

    def test_approval_carries_interviewer_2_onto_the_interview(self):
        request_obj = self._request(InterviewRequest.Status.AWAITING_HR_APPROVAL)
        slot = request_obj.slots.create(start_datetime=self.start)
        request_obj.candidate_selected_slot = slot
        request_obj.calendar_event_id = 'EVT-1'
        request_obj.save(update_fields=['candidate_selected_slot', 'calendar_event_id'])
        self.client.post(reverse('interview_request_approve', args=[request_obj.pk]))
        interview = Interview.objects.get(candidate=self.candidate)
        self.assertEqual(interview.co_interviewer, self.co)
        self.assertEqual(interview.calendar_event_id, 'EVT-1')

    def test_send_invite_keeps_interviewer_2_on_the_meeting(self):
        interview = Interview.objects.create(
            candidate=self.candidate, interviewer=self.lead, co_interviewer=self.co,
            scheduled_date=self.start, calendar_event_id='EVT-1')
        from . import calendar_sync
        with mock.patch('interviews.calendar_sync.requests.post', return_value=_calendar_reply()) as post:
            calendar_sync.send_invite(interview, to_email='rose@example.com', cc_emails=[],
                                      subject='Invite', body='Hello')
        emails = [a['emailAddress']['address'] for a in post.call_args.kwargs['json']['event']['attendees']]
        self.assertEqual(emails, ['lead@turnb.com', 'co@turnb.com', 'rose@example.com'])

    # ---- clash checks ----

    def test_proposed_slot_is_refused_when_interviewer_2_is_busy(self):
        other = Candidate.objects.create(full_name='Other', email='o@example.com', job=self.job)
        Interview.objects.create(candidate=other, interviewer=self.hr, co_interviewer=self.co,
                                 scheduled_date=self.start)
        form = InterviewSlotProposalForm({
            'slot_1_0': self.start.strftime('%Y-%m-%d'), 'slot_1_1': timezone.localtime(self.start).strftime('%H:%M'),
            'slot_2_0': (self.start + timedelta(days=1)).strftime('%Y-%m-%d'),
            'slot_2_1': timezone.localtime(self.start).strftime('%H:%M'),
        }, interviewer=self.lead, co_interviewer=self.co)
        self.assertFalse(form.is_valid())
        self.assertIn('Interviewer 2', str(form.errors))
        self.assertIn('Anu', str(form.errors))

    def test_interviewer_2_role_counts_as_busy_for_their_own_interviews(self):
        other = Candidate.objects.create(full_name='Other', email='o@example.com', job=self.job)
        Interview.objects.create(candidate=other, interviewer=self.lead, co_interviewer=self.co,
                                 scheduled_date=self.start)
        self.assertTrue(Interview.conflicts_for(self.co, self.start).exists())

    # ---- portal: view-only ----

    def test_interviewer_2_sees_the_candidate_and_cv_but_cannot_act(self):
        request_obj = self._request()
        self.client.force_login(self.co)
        home = self.client.get(reverse('interviewer_home'))
        self.assertContains(home, 'Rose E G')
        self.assertContains(home, "You're Interviewer 2")
        self.assertNotContains(home, reverse('interviewer_propose_slots', args=[request_obj.pk]))
        self.assertEqual(self.client.get(reverse('interviewer_candidate', args=[self.candidate.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse('interviewer_propose_slots', args=[request_obj.pk])).status_code, 404)
        with mock.patch('candidates.views.requests.get') as get:
            get.return_value = mock.Mock(status_code=200, content=b'%PDF', headers={'Content-Type': 'application/pdf'})
            get.return_value.raise_for_status = mock.Mock()
            self.assertEqual(self.client.get(reverse('candidate_cv', args=[self.candidate.pk])).status_code, 200)

    def test_interviewer_2_cannot_record_the_result(self):
        interview = Interview.objects.create(
            candidate=self.candidate, interviewer=self.lead, co_interviewer=self.co,
            scheduled_date=timezone.now() - timedelta(hours=1))
        self.client.force_login(self.co)
        home = self.client.get(reverse('interviewer_home'))
        self.assertNotContains(home, reverse('interview_result', args=[interview.pk]))
        self.assertEqual(self.client.get(reverse('interview_result', args=[interview.pk])).status_code, 404)

    # ---- cancel ----

    def test_cancelling_tells_both_interviewers(self):
        request_obj = self._request()
        with mock.patch('interviews.slot_emails.logic_app_mail.send_email') as send:
            self.client.post(reverse('interview_request_cancel', args=[request_obj.pk]))
        self.assertEqual(sorted(c.kwargs['to_email'] for c in send.call_args_list),
                         ['co@turnb.com', 'lead@turnb.com'])
