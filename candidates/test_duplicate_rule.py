"""6-month same-vacancy repeat rule (candidates/duplicate_rule.py).

    DB_ENGINE=sqlite python manage.py test candidates.test_duplicate_rule
"""
from datetime import datetime, timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from jobs.models import Job

from . import duplicate_rule
from .models import PLACEHOLDER_EMAIL_DOMAIN, Candidate, DuplicateRuleState

S = Candidate.Status


class DuplicateRuleTests(TestCase):
    def setUp(self):
        self.job = Job.objects.create(title='Sales Associate', job_code='SA1', status=Job.Status.OPEN)
        self.other_job = Job.objects.create(title='Analyst', job_code='AN1', status=Job.Status.OPEN)
        DuplicateRuleState.objects.all().delete()
        duplicate_rule.run_pending()  # seeds the mark at "now"

    def _cand(self, status=S.OPEN, job=None, email='rose@example.com', days_ago=0):
        c = Candidate.objects.create(full_name='Rose', email=email, job=job or self.job, status=status)
        if days_ago:
            Candidate.objects.filter(pk=c.pk).update(created_at=timezone.now() - timedelta(days=days_ago))
        return c

    def _status(self, c):
        c.refresh_from_db()
        return c.status

    def test_earlier_rejected_rejects_the_new_one(self):
        old = self._cand(S.REJECTED, days_ago=60)
        new = self._cand()
        duplicate_rule.run_pending()
        self.assertEqual(self._status(new), S.REJECTED)
        self.assertEqual(self._status(old), S.REJECTED)
        row = new.history.order_by('-pk').first()
        self.assertEqual(row.performed_by, 'Duplicate Check')
        self.assertIn(old.candidate_code, row.remarks)

    def test_earlier_in_process_rejects_the_new_one_and_keeps_the_original(self):
        for status in (S.SHORTLISTED, S.ROUND1, S.INTERVIEW, S.FINAL_SELECTION, S.SCREENING_HOLD):
            with self.subTest(status=status):
                old = self._cand(status, email=f'{status}@example.com', days_ago=10)
                new = self._cand(email=f'{status}@example.com')
                duplicate_rule.run_pending()
                self.assertEqual(self._status(new), S.REJECTED)
                self.assertEqual(self._status(old), status)

    def test_earlier_open_is_rejected_and_the_new_one_kept(self):
        old = self._cand(S.OPEN, days_ago=20)
        new = self._cand()
        duplicate_rule.run_pending()
        self.assertEqual(self._status(old), S.REJECTED)
        self.assertEqual(self._status(new), S.OPEN)
        self.assertIn(new.candidate_code, old.history.order_by('-pk').first().remarks)

    def test_kept_application_is_not_rejected_on_a_later_run(self):
        self._cand(S.OPEN, days_ago=20)
        new = self._cand()
        duplicate_rule.run_pending()
        duplicate_rule.run_pending()
        self.assertEqual(self._status(new), S.OPEN)

    def test_earlier_hired_does_nothing(self):
        self._cand(S.HIRED, days_ago=30)
        new = self._cand()
        duplicate_rule.run_pending()
        self.assertEqual(self._status(new), S.OPEN)

    def test_older_than_six_months_is_ignored(self):
        old = self._cand(S.REJECTED, days_ago=200)
        new = self._cand()
        duplicate_rule.run_pending()
        self.assertEqual(self._status(new), S.OPEN)
        self.assertEqual(self._status(old), S.REJECTED)

    def test_a_different_vacancy_is_ignored(self):
        old = self._cand(S.OPEN, job=self.other_job, days_ago=5)
        new = self._cand()
        duplicate_rule.run_pending()
        self.assertEqual(self._status(new), S.OPEN)
        self.assertEqual(self._status(old), S.OPEN)

    def test_email_match_ignores_case(self):
        self._cand(S.REJECTED, email='Rose@Example.com', days_ago=5)
        new = self._cand(email='rose@example.com')
        duplicate_rule.run_pending()
        self.assertEqual(self._status(new), S.REJECTED)

    def test_general_application_and_placeholder_emails_are_skipped(self):
        general = Job.objects.create(title='General Application', status=Job.Status.OPEN)
        self._cand(S.REJECTED, job=general, days_ago=5)
        g = self._cand(job=general)
        self._cand(S.REJECTED, email=f'pending-abc@{PLACEHOLDER_EMAIL_DOMAIN}', days_ago=5)
        p = self._cand(email=f'pending-abc@{PLACEHOLDER_EMAIL_DOMAIN}')
        duplicate_rule.run_pending()
        self.assertEqual(self._status(g), S.OPEN)
        self.assertEqual(self._status(p), S.OPEN)

    def test_existing_candidates_are_never_touched(self):
        """Only candidates created after the mark are checked."""
        DuplicateRuleState.objects.all().delete()
        self._cand(S.REJECTED, days_ago=30)
        existing_repeat = self._cand()
        duplicate_rule.run_pending()  # seeds the mark past both
        duplicate_rule.run_pending()
        self.assertEqual(self._status(existing_repeat), S.OPEN)

    def test_months_before_clamps_to_month_end(self):
        tz = timezone.get_current_timezone()
        self.assertEqual(duplicate_rule.months_before(datetime(2026, 8, 31, tzinfo=tz), 6).date(),
                         datetime(2026, 2, 28).date())
        self.assertEqual(duplicate_rule.months_before(datetime(2026, 3, 15, tzinfo=tz), 6).date(),
                         datetime(2025, 9, 15).date())


class DuplicateRuleMiddlewareTests(TestCase):
    """A careers-mailbox application (inserted by the stored procedure, no
    Python involved) is checked before HR's next page loads."""

    def test_signed_in_page_runs_the_check(self):
        job = Job.objects.create(title='Sales Associate', status=Job.Status.OPEN)
        DuplicateRuleState.objects.all().delete()
        duplicate_rule.run_pending()
        Candidate.objects.create(full_name='R', email='r@example.com', job=job, status=S.REJECTED)
        new = Candidate.objects.create(full_name='R', email='r@example.com', job=job)
        cache.delete(duplicate_rule.DuplicateRuleMiddleware.CACHE_KEY)
        user = get_user_model().objects.create_superuser('hr', 'hr@example.com', 'pw')
        self.client.force_login(user)
        self.client.get(reverse('hr_dashboard'))
        new.refresh_from_db()
        self.assertEqual(new.status, S.REJECTED)
