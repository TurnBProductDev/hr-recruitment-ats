"""Emails page (edit every email template in-app) and the Online/Offline
interview invite split.

    DB_ENGINE=sqlite python manage.py test dashboard.test_email_templates_page
"""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from candidates.models import Candidate
from candidates.permissions import HIRING_MANAGER, INTERVIEWER
from email_templates.models import EmailTemplate
from interviews import invites
from interviews.models import Interview
from jobs.models import Job


class EmailsPageTests(TestCase):
    def setUp(self):
        self.hm = get_user_model().objects.create_user('hm', 'hm@turnb.com', 'pw', first_name='Hiring',
                                                       last_name='Manager')
        self.hm.groups.add(Group.objects.get_or_create(name=HIRING_MANAGER)[0])
        self.client.force_login(self.hm)
        self.rejection = EmailTemplate.objects.get(key='rejection')

    def test_lists_all_twelve_templates_and_is_in_the_menu(self):
        response = self.client.get(reverse('email_template_list'))
        self.assertEqual(len(response.context['templates']), 12)
        self.assertContains(response, 'Interview Invite (Online)')
        self.assertContains(response, 'Interview Invite (Offline)')
        self.assertContains(response, reverse('email_template_list'))  # account menu link

    def test_hiring_manager_can_edit_and_save(self):
        url = reverse('email_template_edit', args=[self.rejection.pk])
        response = self.client.post(url, {'subject': 'Update - {candidate_name}',
                                          'body': 'Dear {candidate_name}, thank you.'})
        self.assertRedirects(response, reverse('email_template_list'))
        self.rejection.refresh_from_db()
        self.assertEqual(self.rejection.subject, 'Update - {candidate_name}')
        self.assertEqual(self.rejection.updated_by, self.hm)

    def test_unknown_placeholder_is_refused(self):
        url = reverse('email_template_edit', args=[self.rejection.pk])
        response = self.client.post(url, {'subject': 'Hi', 'body': 'Dear {candiate_name}'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Not a placeholder this email supports: {candiate_name}')
        self.rejection.refresh_from_db()
        self.assertNotIn('candiate', self.rejection.body)

    def test_every_template_saves_unchanged(self):
        for t in EmailTemplate.objects.all():
            with self.subTest(t.key):
                response = self.client.post(reverse('email_template_edit', args=[t.pk]),
                                            {'subject': t.subject, 'body': t.body})
                self.assertEqual(response.status_code, 302, response.content[:300])

    def test_interviewer_cannot_open_it(self):
        interviewer = get_user_model().objects.create_user('int', 'i@turnb.com', 'pw')
        interviewer.groups.add(Group.objects.get_or_create(name=INTERVIEWER)[0])
        self.client.force_login(interviewer)
        self.assertEqual(self.client.get(reverse('email_template_list')).status_code, 403)


class InviteSplitTests(TestCase):
    def setUp(self):
        self.sender = get_user_model().objects.create_user('hr', 'hr@turnb.com', 'pw', first_name='Amrita')
        job = Job.objects.create(job_code='SA1', title='Sales Associate')
        self.candidate = Candidate.objects.create(full_name='Rose E G', email='rose@example.com', job=job)

    def _interview(self, mode, link=''):
        return Interview.objects.create(candidate=self.candidate, mode=mode, meeting_link=link or None,
                                        scheduled_date=timezone.now() + timezone.timedelta(days=2))

    def test_onsite_uses_the_offline_template_with_the_office_address(self):
        body = invites.default_body(self._interview(Interview.Mode.ONSITE), self.sender)
        self.assertIn('Level 3, Oberon Mall, Edapally, Cochin', body)
        self.assertNotIn('stable internet', body)
        self.assertNotIn('Join link', body)
        self.assertIn('an in-person interview at our office', body)

    def test_video_uses_the_online_template_with_the_link(self):
        body = invites.default_body(self._interview(Interview.Mode.VIDEO, 'https://teams/x'), self.sender)
        self.assertIn('Join link: https://teams/x', body)
        self.assertIn('stable internet', body)
        self.assertNotIn('Oberon Mall', body)

    def test_editing_the_offline_template_reaches_onsite_invites(self):
        row = EmailTemplate.objects.get(key='interview_invite_offline')
        row.subject = 'Walk-in interview - {role}'
        row.save()
        self.assertEqual(invites.default_subject(self._interview(Interview.Mode.ONSITE)),
                         'Walk-in interview - Sales Associate')
        self.assertNotEqual(invites.default_subject(self._interview(Interview.Mode.VIDEO)),
                            'Walk-in interview - Sales Associate')
