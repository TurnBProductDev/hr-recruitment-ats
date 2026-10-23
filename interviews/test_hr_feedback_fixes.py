"""HR feedback round (2026-10-23):
1. links in emails are clickable;
2. approving the candidate's slot opens the invite for review, and the
   invite is actually sent (from the popup, or the Scheduler's review page);
3. an interviewer's result emails HR/the recruiter;
4. interview scores are out of 10.

Run against sqlite so the live Azure DB is never touched:
    DB_ENGINE=sqlite python manage.py test interviews
"""
import base64
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from candidates import logic_app_mail, services
from candidates.models import Candidate
from candidates.permissions import HR_ADMIN, INTERVIEWER
from jobs.models import Job

from .models import Interview, InterviewRequest

AJAX = {'HTTP_X_REQUESTED_WITH': 'XMLHttpRequest'}


@override_settings(LOGIC_APP_EMAIL_SENDER_URL='https://mail.example/trigger')
class ClickableLinksTests(TestCase):
    def _sent_body(self, body):
        ok = mock.Mock(status_code=200)
        with mock.patch('candidates.logic_app_mail.requests.post', return_value=ok) as post:
            logic_app_mail.send_email(to_email='a@b.com', subject='s', body=body)
        return post.call_args.kwargs['json']['body']

    def test_urls_become_links_without_eating_the_full_stop(self):
        body = self._sent_body('Sign in here: https://turnb-ats.azurewebsites.net/interviewer/login/.\nThanks')
        self.assertIn('<a href="https://turnb-ats.azurewebsites.net/interviewer/login/">'
                      'https://turnb-ats.azurewebsites.net/interviewer/login/</a>.<br>Thanks', body)

    def test_text_is_still_escaped(self):
        body = self._sent_body('Rose <b>& Co</b> https://x.example/a?b=1&c=2')
        self.assertIn('Rose &lt;b&gt;&amp; Co&lt;/b&gt;', body)
        self.assertIn('<a href="https://x.example/a?b=1&amp;c=2">', body)


class _People(TestCase):
    def setUp(self):
        self.hr = get_user_model().objects.create_user('hr1', 'hr1@turnb.com', 'pw', first_name='Amrita')
        self.hr.groups.add(Group.objects.get_or_create(name=HR_ADMIN)[0])
        self.interviewer = get_user_model().objects.create_user(
            'panel', 'panel@turnb.com', 'pw', first_name='Sreejith', last_name='K R')
        self.interviewer.groups.add(Group.objects.get_or_create(name=INTERVIEWER)[0])
        self.job = Job.objects.create(job_code='J1', title='Program Manager')
        self.candidate = Candidate.objects.create(full_name='Rose E G', email='rose@example.com', job=self.job)
        self.start = (timezone.now() + timedelta(days=2)).replace(second=0, microsecond=0)


class ApproveAndReviewInviteTests(_People):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.hr)
        self.request_obj = InterviewRequest.objects.create(
            candidate=self.candidate, round_type=Interview.RoundType.ROUND1, interviewer=self.interviewer,
            status=InterviewRequest.Status.AWAITING_HR_APPROVAL, created_by=self.hr)
        slot = self.request_obj.slots.create(start_datetime=self.start)
        self.request_obj.candidate_selected_slot = slot
        self.request_obj.save(update_fields=['candidate_selected_slot'])
        self.url = reverse('interview_request_approve', args=[self.request_obj.pk])

    def test_approve_opens_a_confirmation_in_the_popup(self):
        response = self.client.get(self.url, **AJAX)
        self.assertContains(response, 'Approve &amp; Review Email')
        self.assertContains(response, timezone.localtime(self.start).strftime('%d %b %Y, %H:%M'))
        self.assertFalse(self.candidate.interviews.exists())  # nothing scheduled yet

    def test_approving_returns_an_editable_invite_with_send_enabled(self):
        with mock.patch('interviews.invites.logic_app_mail.send_email') as send:
            response = self.client.post(self.url, **AJAX)
        send.assert_not_called()  # HR reviews first
        self.assertContains(response, 'name="to_email"')
        self.assertNotContains(response, 'readonly>\n  </div>\n  <div class="mb-2">\n    <label class="form-label small mb-1">CC')
        self.assertContains(response, '<button type="submit" class="btn btn-fill-app">')
        self.assertNotContains(response, 'hidden class="btn btn-fill-app"')
        self.assertEqual(self.candidate.interviews.count(), 1)

    def test_send_invite_uses_hrs_edited_text(self):
        self.client.post(self.url, **AJAX)
        interview = self.candidate.interviews.get()
        with mock.patch('interviews.invites.logic_app_mail.send_email') as send:
            response = self.client.post(reverse('interview_send_invite', args=[interview.pk]), {
                'to_email': 'rose.personal@example.com', 'subject': 'Edited subject', 'body': 'Edited body'}, **AJAX)
        self.assertEqual(response.json(), {'ok': True})
        kwargs = send.call_args.kwargs
        self.assertEqual((kwargs['to_email'], kwargs['subject'], kwargs['body']),
                         ('rose.personal@example.com', 'Edited subject', 'Edited body'))

    def test_card_button_opens_the_popup_instead_of_posting(self):
        services.change_status(self.candidate, Candidate.Status.ROUND1)  # the Round 1 card is showing
        page = self.client.get(reverse('candidate_timeline', args=[self.candidate.pk]))
        self.assertContains(page, f'data-load-url="{self.url}" data-modal-title="Approve Interview"')


class SchedulerReviewPageTests(_People):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.hr)
        self.interview = Interview.objects.create(
            candidate=self.candidate, round_type=Interview.RoundType.ROUND1,
            interviewer=self.interviewer, scheduled_date=self.start, created_by=self.hr)

    def test_scheduler_links_to_a_review_page_instead_of_sending(self):
        page = self.client.get(reverse('interview_scheduler'))
        self.assertContains(page, 'Review &amp; Send Invite')
        review = self.client.get(reverse('interview_send_invite', args=[self.interview.pk]))
        self.assertContains(review, 'Send Invite - Rose E G')
        self.assertContains(review, 'value="rose@example.com"')

    def test_ics_leaves_out_an_interviewer_who_already_has_the_teams_meeting(self):
        self.interview.calendar_event_id = 'EVT-1'
        self.interview.save(update_fields=['calendar_event_id'])
        with mock.patch('interviews.invites.logic_app_mail.send_email') as send:
            self.client.post(reverse('interview_send_invite', args=[self.interview.pk]), {
                'to_email': 'rose@example.com', 'subject': 's', 'body': 'b'})
        kwargs = send.call_args.kwargs
        self.assertIn('panel@turnb.com', kwargs['cc_emails'])  # still copied on the email
        # .ics folds long lines ("\r\n " continues the previous one).
        ics = base64.b64decode(kwargs['attachments'][0]['ContentBytes']).decode().replace('\r\n ', '')
        self.assertIn('rose@example.com', ics)
        self.assertNotIn('panel@turnb.com', ics)


class InterviewerResultEmailsHrTests(_People):
    def setUp(self):
        super().setUp()
        self.interview = Interview.objects.create(
            candidate=self.candidate, round_type=Interview.RoundType.ROUND1,
            interviewer=self.interviewer, scheduled_date=self.start, created_by=self.hr)

    def _mark(self, user, **extra):
        self.client.force_login(user)
        data = {'status': Interview.Status.COMPLETED, 'result': Interview.Result.PASS_,
                'score': '8.5', 'feedback': 'Strong on SQL.', **extra}
        return self.client.post(reverse('interview_result', args=[self.interview.pk]), data)

    def test_interviewer_result_emails_the_hr_who_scheduled_it(self):
        with mock.patch('interviews.slot_emails.logic_app_mail.send_email') as send:
            self._mark(self.interviewer)
        kwargs = send.call_args.kwargs
        self.assertEqual(kwargs['to_email'], 'hr1@turnb.com')
        self.assertIn('Move to Next Round', kwargs['subject'])
        self.assertIn('Interview score: 8.5 / 10', kwargs['body'])
        self.assertIn('Feedback: Strong on SQL.', kwargs['body'])
        self.assertIn(reverse('candidate_timeline', args=[self.candidate.pk]), kwargs['body'])
        self.assertTrue(self.hr.notifications.filter(title='Interview result - Rose E G').exists())

    def test_hr_marking_the_result_themselves_sends_nothing(self):
        with mock.patch('interviews.slot_emails.logic_app_mail.send_email') as send:
            self._mark(self.hr)
        send.assert_not_called()

    def test_without_an_hr_user_it_goes_to_the_standing_hr_address(self):
        self.interview.created_by = None
        self.interview.save(update_fields=['created_by'])
        with mock.patch('interviews.slot_emails.logic_app_mail.send_email') as send:
            self._mark(self.interviewer)
        self.assertEqual(send.call_args.kwargs['to_email'], logic_app_mail.default_cc_list()[-1])


class InterviewerDropdownTests(_People):
    """Two accounts named DILSHAD M N - an old, deactivated one and the live
    one - made the allocation go to the wrong (old) mailbox."""

    def setUp(self):
        super().setUp()
        self.old = get_user_model().objects.create_user(
            'Dillu', 'old@gmail.com', 'pw', first_name='DILSHAD', last_name='M N', is_active=False)
        self.old.groups.add(Group.objects.get_or_create(name=INTERVIEWER)[0])

    def _choices(self, form):
        return {label for value, label in form.fields['interviewer'].choices if value}

    def test_deactivated_accounts_are_not_offered_and_labels_show_the_email(self):
        from .forms import InterviewAllocationForm
        labels = self._choices(InterviewAllocationForm(candidate=self.candidate))
        self.assertIn('Sreejith K R (panel@turnb.com)', labels)
        self.assertNotIn('DILSHAD M N (old@gmail.com)', labels)

    def test_an_existing_interview_keeps_its_deactivated_interviewer(self):
        from .forms import InterviewForm
        interview = Interview.objects.create(candidate=self.candidate, interviewer=self.old,
                                             scheduled_date=self.start)
        self.assertIn('DILSHAD M N (old@gmail.com)',
                      self._choices(InterviewForm(instance=interview, candidate=self.candidate)))


class ScoreOutOfTenTests(InterviewerResultEmailsHrTests):
    def test_form_says_out_of_10(self):
        self.client.force_login(self.hr)
        page = self.client.get(reverse('interview_result', args=[self.interview.pk]))
        self.assertContains(page, '(out of 10)')
        self.assertContains(page, 'max="10"')

    def test_score_above_10_is_rejected(self):
        with mock.patch('interviews.slot_emails.logic_app_mail.send_email'):
            response = self._mark(self.interviewer, score='11')
        self.assertContains(response, 'Enter a score from 0 to 10.')
        self.interview.refresh_from_db()
        self.assertIsNone(self.interview.score)
