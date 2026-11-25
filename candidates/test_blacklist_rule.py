"""Blacklisted once = auto-rejected forever (candidates/duplicate_rule.py).

    DB_ENGINE=sqlite python manage.py test candidates.test_blacklist_rule
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from jobs.models import Job

from . import duplicate_rule, services
from .models import Candidate, DuplicateRuleState, EmailRegistry
from .reject_reasons import BLACKLISTED_EARLIER

S = Candidate.Status
EMAIL = 'bad@example.com'


class BlacklistRuleTests(TestCase):
    def setUp(self):
        self.job = Job.objects.create(title='Sales Associate', job_code='SA1', status=Job.Status.OPEN)
        self.other_job = Job.objects.create(title='Analyst', job_code='AN1', status=Job.Status.OPEN)
        self.general = Job.objects.create(title='General Application', job_code='GA1', status=Job.Status.OPEN)
        DuplicateRuleState.objects.all().delete()
        duplicate_rule.run_pending()  # seeds the mark at "now"
        self.user = get_user_model().objects.create_superuser('hr', 'hr@example.com', 'pw')
        self.client.force_login(self.user)

    def _apply(self, job=None, email=EMAIL, days_ago=0):
        """A new application through the careers-form path."""
        c = Candidate(full_name='Bad', email=email, job=job or self.job)
        services.submit_application(c)
        if days_ago:
            Candidate.objects.filter(pk=c.pk).update(created_at=timezone.now() - timedelta(days=days_ago))
        c.refresh_from_db()
        return c

    def _blacklisted_original(self):
        original = self._apply()
        services.blacklist_candidate(original, 'Fake documents', user=self.user, performed_by='HR')
        return original

    def test_any_later_application_is_rejected_with_no_time_limit(self):
        original = self._blacklisted_original()
        Candidate.objects.filter(pk=original.pk).update(created_at=timezone.now() - timedelta(days=3 * 365))
        for job in (self.job, self.other_job, self.general):
            with self.subTest(job=job.title):
                new = self._apply(job=job)
                self.assertEqual(new.status, S.REJECTED)
                self.assertTrue(new.is_blacklisted)
                row = new.history.order_by('-pk').first()
                self.assertEqual(row.performed_by, 'Blacklist Check')
                self.assertEqual(row.reject_reason, BLACKLISTED_EARLIER)
                self.assertFalse(new.history.filter(new_status=S.BLACKLISTED).exists())
        original.refresh_from_db()
        self.assertEqual(original.status, S.BLACKLISTED)

    def test_still_rejected_after_the_blacklisted_record_is_deleted(self):
        self._blacklisted_original().delete()
        self.assertIsNotNone(EmailRegistry.objects.get(email=EMAIL).blacklisted_at)
        self.assertEqual(self._apply(job=self.other_job).status, S.REJECTED)

    def test_email_case_is_ignored(self):
        self._blacklisted_original()
        self.assertEqual(self._apply(email='BAD@Example.com').status, S.REJECTED)

    def test_mailbox_insert_marked_blacklisted_is_turned_into_rejected(self):
        """The stored procedure before this change inserted it as BLACKLISTED."""
        self._blacklisted_original()
        new = Candidate.objects.create(full_name='Bad', email=EMAIL, job=self.job,
                                       status=S.BLACKLISTED, is_blacklisted=True)
        duplicate_rule.run_pending()
        new.refresh_from_db()
        self.assertEqual(new.status, S.REJECTED)

    def test_mailbox_insert_open_is_rejected(self):
        self._blacklisted_original()
        new = Candidate.objects.create(full_name='Bad', email=EMAIL, job=self.job)
        duplicate_rule.run_pending()
        new.refresh_from_db()
        self.assertEqual(new.status, S.REJECTED)

    def test_record_hr_blacklists_before_the_sweep_is_left_alone(self):
        fresh = Candidate.objects.create(full_name='Bad', email=EMAIL, job=self.job)
        services.blacklist_candidate(fresh, 'Abusive', user=self.user)
        duplicate_rule.run_pending()
        fresh.refresh_from_db()
        self.assertEqual(fresh.status, S.BLACKLISTED)

    def test_other_emails_are_unaffected(self):
        self._blacklisted_original()
        self.assertEqual(self._apply(email='good@example.com').status, S.OPEN)

    # --- locked: can't come back to Open -------------------------------------

    def _locked(self):
        self._blacklisted_original()
        new = self._apply(job=self.other_job)
        self.assertTrue(services.locked_by_blacklist(new))
        return new

    def test_reopen_is_refused(self):
        new = self._locked()
        self.client.post(reverse('candidate_reopen', args=[new.pk]), {'reason': 'try again'})
        new.refresh_from_db()
        self.assertEqual(new.status, S.REJECTED)

    def test_update_status_box_is_refused(self):
        new = self._locked()
        self.client.post(reverse('candidate_set_status', args=[new.pk]),
                         {'status': S.SHORTLISTED, 'reason': 'looks good'})
        new.refresh_from_db()
        self.assertEqual(new.status, S.REJECTED)

    def test_undo_is_refused(self):
        new = self._locked()
        self.client.post(reverse('candidate_revert', args=[new.pk]))
        new.refresh_from_db()
        self.assertEqual(new.status, S.REJECTED)

    def test_undoing_the_original_blacklisting_lifts_the_rule(self):
        original = self._blacklisted_original()
        self.client.post(reverse('candidate_revert', args=[original.pk]))
        original.refresh_from_db()
        self.assertNotEqual(original.status, S.BLACKLISTED)
        self.assertIsNone(EmailRegistry.objects.get(email=EMAIL).blacklisted_at)
        self.assertEqual(self._apply(job=self.other_job).status, S.OPEN)
