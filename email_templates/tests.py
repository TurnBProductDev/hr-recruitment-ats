from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from candidates.models import Candidate
from candidates.rejection_emails import default_body, default_subject
from interviews.invites import default_body as invite_body, default_subject as invite_subject
from interviews.models import Interview, InterviewRequest
from interviews.slot_emails import notify_hr_slots_proposed, notify_interviewer_new_request
from jobs.models import Job

from .models import EmailTemplate
from .store import render_email


class EmailTemplateSeedTests(TestCase):
    def test_all_six_are_seeded(self):
        keys = set(EmailTemplate.objects.values_list('key', flat=True))
        self.assertEqual(keys, {
            'rejection', 'interview_invite', 'interviewer_new_request',
            'hr_slots_proposed', 'interviewer_new_slots_needed', 'password_reset',
        })

    def test_rejection_seed_matches_todays_wording(self):
        row = EmailTemplate.objects.get(key='rejection')
        self.assertEqual(row.subject, 'Update on your application to TurnB Business Services')
        self.assertIn('We wish you every success in your future endeavors!', row.body)


class RenderEmailTests(TestCase):
    def test_falls_back_to_default_if_no_row(self):
        subject, body = render_email('does-not-exist', 'Hi {name}', 'Body {name}', name='Rose')
        self.assertEqual(subject, 'Hi Rose')
        self.assertEqual(body, 'Body Rose')

    def test_unknown_placeholder_is_left_literal_not_raised(self):
        subject, body = render_email('does-not-exist', 'Hi {typo}', 'Body', name='Rose')
        self.assertEqual(subject, 'Hi {typo}')


@override_settings(LOGIC_APP_EMAIL_SENDER_URL='https://logic.example/send-email')
class EmailCallSiteWiringTests(TestCase):
    """An edited row must actually change what gets sent - not just sit in
    the database."""

    def setUp(self):
        self.job = Job.objects.create(job_code='J1', title='Recruiter')
        self.candidate = Candidate.objects.create(
            full_name='Rose E G', email='rose@example.com', job=self.job)

    def test_editing_rejection_body_changes_what_default_body_returns(self):
        row = EmailTemplate.objects.get(key='rejection')
        row.body = 'CUSTOM MARKER BODY for {candidate_name}.'
        row.save()
        self.assertEqual(default_body(self.candidate), 'CUSTOM MARKER BODY for Rose E G.')
        self.assertEqual(default_subject(self.candidate), 'Update on your application to TurnB Business Services')

    def test_editing_invite_subject_changes_what_default_subject_returns(self):
        interviewer = get_user_model().objects.create_user('panel', 'panel@turnb.com', 'pw')
        interview = Interview.objects.create(
            candidate=self.candidate, interviewer=interviewer,
            round_type=Interview.RoundType.ROUND1,
            scheduled_date=timezone.now() + timezone.timedelta(days=1))
        row = EmailTemplate.objects.get(key='interview_invite')
        row.subject = 'CUSTOM SUBJECT - {role}'
        row.save()
        self.assertEqual(invite_subject(interview), 'CUSTOM SUBJECT - Recruiter')
        self.assertIn('Rose E G', invite_body(interview, interviewer))

    def test_editing_interviewer_new_request_body_reaches_the_sent_email(self):
        interviewer = get_user_model().objects.create_user('panel', 'panel@turnb.com', 'pw')
        request_obj = InterviewRequest.objects.create(
            candidate=self.candidate, round_type=Interview.RoundType.ROUND1, interviewer=interviewer)
        row = EmailTemplate.objects.get(key='interviewer_new_request')
        row.body = 'CUSTOM MARKER for {candidate_name}.'
        row.save()
        with mock.patch('candidates.logic_app_mail.requests.post',
                         return_value=mock.Mock(status_code=200, text='')) as post:
            notify_interviewer_new_request(request_obj)
        self.assertEqual(post.call_args.kwargs['json']['body'], 'CUSTOM MARKER for Rose E G.')

    def test_editing_hr_slots_proposed_body_reaches_the_sent_email(self):
        hr_user = get_user_model().objects.create_user('hr', 'hr@turnb.com', 'pw')
        interviewer = get_user_model().objects.create_user('panel', 'panel@turnb.com', 'pw')
        request_obj = InterviewRequest.objects.create(
            candidate=self.candidate, round_type=Interview.RoundType.ROUND1,
            interviewer=interviewer, created_by=hr_user)
        row = EmailTemplate.objects.get(key='hr_slots_proposed')
        row.body = 'CUSTOM MARKER, slots: {slot_lines}'
        row.save()
        with mock.patch('candidates.logic_app_mail.requests.post',
                         return_value=mock.Mock(status_code=200, text='')) as post:
            notify_hr_slots_proposed(request_obj)
        self.assertIn('CUSTOM MARKER, slots:', post.call_args.kwargs['json']['body'])


class EmailTemplateAdminTests(TestCase):
    def setUp(self):
        self.admin_user = get_user_model().objects.create_superuser('root', 'root@turnb.com', 'pw')
        self.client.force_login(self.admin_user)

    def test_add_is_disabled(self):
        response = self.client.get(reverse('admin:email_templates_emailtemplate_add'))
        self.assertEqual(response.status_code, 403)

    def test_change_is_allowed_and_sets_updated_by(self):
        row = EmailTemplate.objects.get(key='password_reset')
        response = self.client.post(
            reverse('admin:email_templates_emailtemplate_change', args=[row.pk]),
            {'subject': row.subject, 'body': row.body + '\n\nExtra line.'})
        self.assertEqual(response.status_code, 302)
        row.refresh_from_db()
        self.assertIn('Extra line.', row.body)
        self.assertEqual(row.updated_by, self.admin_user)
