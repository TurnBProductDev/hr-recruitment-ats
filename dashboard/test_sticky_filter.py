"""The Vacancy / open-only filter follows HR across pages (dashboard/sticky_filter.py).

    DB_ENGINE=sqlite python manage.py test dashboard.test_sticky_filter
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from jobs.models import Job

from .sticky_filter import COOKIE


class StickyFilterTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser('hr', 'hr@example.com', 'pw')
        self.client.force_login(self.user)
        self.job = Job.objects.create(title='Sales Associate', job_code='SA12026',
                                      status=Job.Status.OPEN, openings=2)
        self.closed = Job.objects.create(title='Old Role', job_code='OR1',
                                         status=Job.Status.CLOSED, openings=1)

    def _pick(self, job, page='hr_dashboard', **extra):
        params = {'setfilter': '1', 'scoped': '1', 'scope': 'open', 'job': job.pk if job else ''}
        params.update(extra)
        return self.client.get(reverse(page), {k: v for k, v in params.items() if v is not None})

    def test_no_filter_picked_changes_nothing(self):
        for page in ('hr_dashboard', 'candidate_repository', 'interview_scheduler', 'hr_reports'):
            self.assertEqual(self.client.get(reverse(page)).status_code, 200, page)

    def test_vacancy_picked_on_dashboard_follows_to_other_pages(self):
        self._pick(self.job)
        for page in ('candidate_repository', 'interview_scheduler', 'hr_reports', 'scoring_criteria',
                     'hr_dashboard'):
            response = self.client.get(reverse(page))
            self.assertEqual(response.status_code, 302, page)
            self.assertIn(f'job={self.job.pk}', response['Location'], page)
            followed = self.client.get(response['Location'])
            self.assertEqual(followed.status_code, 200, page)

    def test_followed_page_shows_the_filtered_numbers(self):
        self._pick(self.job)
        response = self.client.get(reverse('hr_dashboard'), follow=True)
        self.assertEqual(response.context['selected_job'], str(self.job.pk))
        self.assertEqual(response.context['open_positions'], 2)

    def test_other_query_params_are_kept(self):
        self._pick(self.job)
        response = self.client.get(reverse('candidate_repository'), {'tab': 'rejected', 'q': 'anoob'})
        self.assertIn('tab=rejected', response['Location'])
        self.assertIn('q=anoob', response['Location'])

    def test_choosing_all_vacancies_clears_it(self):
        self._pick(self.job)
        self._pick(None)
        self.assertEqual(self.client.cookies[COOKIE].value, '')  # deleted
        self.assertEqual(self.client.get(reverse('candidate_repository')).status_code, 200)

    def test_repository_clear_filters_link_clears_it(self):
        self._pick(self.job)
        self.client.get(reverse('candidate_repository'),
                        {'tab': 'open', 'setfilter': '1', 'scoped': '1', 'scope': 'open', 'job': ''})
        self.assertEqual(self.client.get(reverse('hr_dashboard')).status_code, 200)

    def test_changing_it_on_another_page_updates_it(self):
        other = Job.objects.create(title='Analyst', status=Job.Status.OPEN, openings=1)
        self._pick(self.job)
        self._pick(other, page='candidate_repository')
        response = self.client.get(reverse('hr_dashboard'))
        self.assertIn(f'job={other.pk}', response['Location'])

    def test_a_drill_down_link_does_not_change_it(self):
        other = Job.objects.create(title='Analyst', status=Job.Status.OPEN, openings=1)
        self._pick(self.job)
        # e.g. a By Vacancy cell linking to another vacancy's interviews
        response = self.client.get(reverse('interview_scheduler'), {'job': other.pk, 'scope': 'open'})
        self.assertEqual(response.status_code, 200)
        self.assertIn(f'job={self.job.pk}', self.client.get(reverse('hr_dashboard'))['Location'])

    def test_open_only_switched_off_is_remembered(self):
        self._pick(None, scope=None)  # switch unchecked -> no scope param
        response = self.client.get(reverse('candidate_repository'))
        self.assertEqual(response.status_code, 302)
        followed = self.client.get(response['Location'])
        self.assertEqual(followed.context['scope'], '')

    def test_closed_vacancy_picked_on_reports_turns_open_only_off(self):
        self.client.get(reverse('hr_reports'), {'setfilter': '1', 'job': self.closed.pk})
        followed = self.client.get(reverse('hr_dashboard'), follow=True)
        self.assertEqual(followed.context['selected_job'], str(self.closed.pk))
        self.assertEqual(followed.context['scope'], '')

    def test_signing_out_forgets_it(self):
        self._pick(self.job)
        self.client.post(reverse('logout'))
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse('hr_dashboard')).status_code, 200)

    def test_cookie_is_a_browser_session_cookie(self):
        response = self._pick(self.job)
        morsel = response.cookies[COOKIE]
        self.assertEqual(morsel['max-age'], '')
        self.assertEqual(morsel['expires'], '')
        self.assertTrue(morsel['httponly'])

    def test_interviews_page_follows_the_vacancy(self):
        self._pick(self.job)
        response = self.client.get(reverse('interview_scheduler'), follow=True)
        self.assertEqual(response.context['filter_job'], self.job)
        self.assertContains(response, 'Clear vacancy filter')
