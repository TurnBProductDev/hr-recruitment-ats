"""Dashboard summary tests.

Run against sqlite so the live Azure DB is never touched:
    DB_ENGINE=sqlite python manage.py test dashboard
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from candidates.models import Candidate
from jobs.models import Job

from .views import OPEN_GROUP, REJECTED_GROUP, SHORTLISTED_GROUP


class StatusBucketTests(TestCase):
    def test_every_status_lands_in_exactly_one_bucket(self):
        """If a new status is added without slotting it into a bucket, the
        By Job / By Source rows silently stop adding up to Total."""
        buckets = list(OPEN_GROUP) + list(SHORTLISTED_GROUP) + list(REJECTED_GROUP)
        all_statuses = [value for value, _label in Candidate.Status.choices]
        self.assertCountEqual(buckets, all_statuses)


class SummaryTableTests(TestCase):
    def setUp(self):
        self.job = Job.objects.create(title='Program Manager')
        self.user = get_user_model().objects.create_superuser('hr', 'hr@example.com', 'pw')
        self.client.force_login(self.user)
        # One candidate in every status, all on the same vacancy and source.
        for index, (status, _label) in enumerate(Candidate.Status.choices):
            Candidate.objects.create(
                full_name=f'C{index}', email=f'c{index}@example.com',
                job=self.job, source='Careers', status=status)

    def _row(self, key):
        response = self.client.get(reverse('hr_dashboard'))
        return response.context[key][0]

    def test_by_job_columns_add_up_to_total(self):
        row = self._row('by_job')
        self.assertEqual(row['total'], 9)
        self.assertEqual(row['open'] + row['shortlisted'] + row['rejected'], row['total'])

    def test_by_job_buckets_have_the_agreed_membership(self):
        row = self._row('by_job')
        self.assertEqual(row['open'], 2)          # Open + Screening Hold
        self.assertEqual(row['shortlisted'], 5)   # Shortlisted, Round 1, Interview, Final, Hired
        self.assertEqual(row['rejected'], 2)      # Rejected + Blacklisted

    def test_by_source_columns_add_up_to_total(self):
        row = self._row('by_source')
        self.assertEqual(row['open'] + row['shortlisted'] + row['rejected'], row['total'])
