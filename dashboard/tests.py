"""Dashboard summary tests.

Run against sqlite so the live Azure DB is never touched:
    DB_ENGINE=sqlite python manage.py test dashboard
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from candidates.models import Candidate
from jobs.models import Job

from .views import _summary_counts_qs


class StatusBucketTests(TestCase):
    def test_every_status_lands_in_exactly_one_bucket(self):
        """If a new status is added without slotting it into a bucket, or a
        candidate matches more than one bucket, the By Job / By Source rows
        would silently stop adding up to Total. Bucketing isn't a pure
        function of status any more (a hold's bucket also depends on
        hold_from_status - see dashboard.views.INITIAL_HOLD), so this checks
        actual rows/counts rather than the status list alone."""
        job = Job.objects.create(title='Bucket Coverage Check')
        for index, (status, _label) in enumerate(Candidate.Status.choices):
            Candidate.objects.create(
                full_name=f'S{index}', email=f's{index}@example.com', job=job, status=status)
        # The hold_from_status=OPEN carve-out isn't distinguishable by status
        # alone, so cover it explicitly too.
        Candidate.objects.create(
            full_name='InitialHold', email='initial-hold@example.com', job=job,
            status=Candidate.Status.SCREENING_HOLD, hold_from_status=Candidate.Status.OPEN)

        counts = _summary_counts_qs(Candidate.objects.filter(job=job))
        self.assertEqual(counts['open'] + counts['shortlisted'] + counts['rejected'] + counts['hired'],
                         counts['total'])


class InitialHoldBucketTests(TestCase):
    """A hold taken before screening counts as Rejected (see
    candidates.views.FutureProspectsListView). A hold taken at any later
    stage counts as Active Pool (Shortlisted), not Open/Unattended - some
    action was already taken to get them there before they were held."""

    def test_initial_hold_counts_as_rejected(self):
        job = Job.objects.create(title='Analyst')
        Candidate.objects.create(
            full_name='Held Early', email='held-early@example.com', job=job,
            status=Candidate.Status.SCREENING_HOLD, hold_from_status=Candidate.Status.OPEN)
        counts = _summary_counts_qs(Candidate.objects.filter(job=job))
        self.assertEqual(counts['rejected'], 1)
        self.assertEqual(counts['open'], 0)
        self.assertEqual(counts['shortlisted'], 0)

    def test_later_stage_hold_counts_as_active_pool_not_open(self):
        job = Job.objects.create(title='Analyst')
        Candidate.objects.create(
            full_name='Held Later', email='held-later@example.com', job=job,
            status=Candidate.Status.SCREENING_HOLD, hold_from_status=Candidate.Status.ROUND1)
        counts = _summary_counts_qs(Candidate.objects.filter(job=job))
        self.assertEqual(counts['shortlisted'], 1)
        self.assertEqual(counts['open'], 0)
        self.assertEqual(counts['rejected'], 0)


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
        self.assertEqual(row['open'] + row['shortlisted'] + row['rejected'] + row['hired'], row['total'])

    def test_by_job_buckets_have_the_agreed_membership(self):
        row = self._row('by_job')
        # setUp's Hold candidate has no hold_from_status recorded (blank, not
        # OPEN), so it isn't an Initial Hold - it counts as Active Pool here,
        # same as any other hold not taken before screening.
        self.assertEqual(row['open'], 1)          # Open
        self.assertEqual(row['shortlisted'], 5)   # Shortlisted, Round 1, Interview, Final, Hold
        self.assertEqual(row['rejected'], 2)      # Rejected + Blacklisted
        self.assertEqual(row['hired'], 1)         # Hired

    def test_by_source_columns_add_up_to_total(self):
        row = self._row('by_source')
        self.assertEqual(row['open'] + row['shortlisted'] + row['rejected'] + row['hired'], row['total'])


class OverviewFunnelTests(TestCase):
    def setUp(self):
        self.job = Job.objects.create(title='Engineer', openings=3)
        self.user = get_user_model().objects.create_superuser('hr2', 'hr2@example.com', 'pw')
        self.client.force_login(self.user)

    def _get(self):
        return self.client.get(f"{reverse('hr_dashboard')}?view=overview")

    def test_stage_names_are_renamed(self):
        # Checked against the funnel context directly, not raw page text -
        # Daily View (a separate feature, rendered in the same response)
        # coincidentally uses some of the old funnel-stage phrasing for its
        # own, unrelated action labels.
        stages = self._get().context['funnel']
        self.assertEqual([s['name'] for s in stages],
                         ['CV Screening', 'Tele Screening', 'Round 1', 'Round 2', 'Hire'])
        self.assertEqual([s['cleared'][0] for s in stages],
                         ['Qualified', 'Shortlisted', 'Cleared', 'Cleared', 'Hired'])

    def test_top_cards_drop_the_hold_card(self):
        response = self._get()
        self.assertNotIn('hold', response.context['funnel_top'])
        self.assertIn('screening_pending', response.context['funnel_top'])

    def test_openings_card_comes_before_total_candidates(self):
        content = self._get().content.decode()
        self.assertLess(content.index('>Openings<'), content.index('>Total Candidates<'))

    def test_initial_hold_appears_as_future_prospects_not_a_hold_segment(self):
        Candidate.objects.create(
            full_name='Held Early', email='held-early@example.com', job=self.job,
            status=Candidate.Status.SCREENING_HOLD, hold_from_status=Candidate.Status.OPEN)
        response = self._get()
        cv_screening = response.context['funnel'][0]
        drop_labels = [d[0] for d in cv_screening['drops']]
        self.assertIn('Future Prospects', drop_labels)

    def test_unable_to_connect_is_folded_into_yet_to_call(self):
        response = self._get()
        tele_screening = response.context['funnel'][1]
        drop_labels = [d[0] for d in tele_screening['drops']]
        self.assertNotIn('Unable to Connect', drop_labels)
