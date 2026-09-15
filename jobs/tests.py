import json
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from candidates.models import ScoringCriteria
from candidates.permissions import HR_ADMIN, RECRUITER
from candidates.views import GENERAL_APPLICATION

from . import jd_extraction
from .models import Job


def _openai_response(description, requirements):
    return {'choices': [{'message': {'content': json.dumps(
        {'description': description, 'requirements': requirements})}}]}


class JDExtractionTests(TestCase):
    """candidates.cv_extraction has its own coverage for the shared PDF
    reading helpers (HR_management/pdf_text.py) - these focus on what's
    specific to reading a JD: the request/response shape and error handling."""

    @mock.patch('jobs.jd_extraction.pdf_text.extract_text', return_value='Full JD text here.')
    @mock.patch('jobs.jd_extraction.requests.post')
    def test_extract_fields_success(self, mock_post, mock_text):
        mock_post.return_value = mock.Mock(
            status_code=200,
            json=lambda: _openai_response('Role overview.', 'Requirement one\nRequirement two'))
        with self.settings(AZURE_OPENAI_ENDPOINT='https://example.test', AZURE_OPENAI_KEY='key'):
            result = jd_extraction.extract_fields(b'%PDF-1.4 fake', title_hint='HRBP')
        self.assertEqual(result, {'description': 'Role overview.', 'requirements': 'Requirement one\nRequirement two'})

    def test_not_configured_raises(self):
        with self.settings(AZURE_OPENAI_ENDPOINT='', AZURE_OPENAI_KEY=''):
            with self.assertRaises(jd_extraction.JDExtractionError):
                jd_extraction.extract_fields(b'%PDF-1.4 fake')

    @mock.patch('jobs.jd_extraction.pdf_text.render_page_images', return_value=[])
    @mock.patch('jobs.jd_extraction.pdf_text.extract_text', return_value='')
    def test_unreadable_file_raises(self, mock_text, mock_images):
        with self.settings(AZURE_OPENAI_ENDPOINT='https://example.test', AZURE_OPENAI_KEY='key'):
            with self.assertRaises(jd_extraction.JDExtractionError):
                jd_extraction.extract_fields(b'not a real pdf')

    @mock.patch('jobs.jd_extraction.pdf_text.extract_text', return_value='Full JD text here.')
    @mock.patch('jobs.jd_extraction.requests.post')
    def test_blank_fields_are_not_an_error(self, mock_post, mock_text):
        """A file that reads fine but has nothing for one field (or both) is
        a valid, if unhelpful, result - not something to raise on."""
        mock_post.return_value = mock.Mock(status_code=200, json=lambda: _openai_response('', ''))
        with self.settings(AZURE_OPENAI_ENDPOINT='https://example.test', AZURE_OPENAI_KEY='key'):
            result = jd_extraction.extract_fields(b'%PDF-1.4 fake')
        self.assertEqual(result, {'description': '', 'requirements': ''})


class JobExtractJDViewTests(TestCase):
    def setUp(self):
        self.hr_user = get_user_model().objects.create_user('hr', password='pw')
        self.hr_user.groups.add(Group.objects.get_or_create(name=HR_ADMIN)[0])
        self.url = reverse('job_extract_jd')
        self.file = SimpleUploadedFile('jd.pdf', b'%PDF-1.4 fake', content_type='application/pdf')

    def test_requires_login(self):
        response = self.client.post(self.url, {'jd_file': self.file, 'title': 'HRBP'})
        self.assertEqual(response.status_code, 302)

    def test_requires_hr_group(self):
        get_user_model().objects.create_user('nobody', password='pw')
        self.client.login(username='nobody', password='pw')
        response = self.client.post(self.url, {'jd_file': self.file, 'title': 'HRBP'})
        self.assertEqual(response.status_code, 403)

    def test_missing_file_is_a_bad_request(self):
        self.client.login(username='hr', password='pw')
        response = self.client.post(self.url, {'title': 'HRBP'})
        self.assertEqual(response.status_code, 400)

    def test_success(self):
        self.client.login(username='hr', password='pw')
        with mock.patch('jobs.views.jd_extraction.extract_fields',
                        return_value={'description': 'Role overview.', 'requirements': 'Req one'}) as extract:
            response = self.client.post(self.url, {'jd_file': self.file, 'title': 'HRBP'})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data, {'status': 'ok', 'description': 'Role overview.', 'requirements': 'Req one'})
        self.assertEqual(extract.call_args.kwargs.get('title_hint'), 'HRBP')

    def test_extraction_error_is_still_http_200(self):
        """Same convention as CVExtractAPIView: a readable-but-empty or
        failed extraction is `status: error` in the body, not a failing HTTP
        status - the JS just leaves the fields as they were."""
        self.client.login(username='hr', password='pw')
        with mock.patch('jobs.views.jd_extraction.extract_fields',
                        side_effect=jd_extraction.JDExtractionError('Could not read this file.')):
            response = self.client.post(self.url, {'jd_file': self.file, 'title': 'HRBP'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'status': 'error', 'message': 'Could not read this file.'})


class JobManageListViewTests(TestCase):
    """General Application isn't a real vacancy - it's just where intake
    files a candidate it couldn't match to an open one (see
    candidates.views.GENERAL_APPLICATION). Still listed here so it can be
    opened, but always sorted last and left out of the vacancy count."""

    def setUp(self):
        self.hr_user = get_user_model().objects.create_user('hr', password='pw')
        self.hr_user.groups.add(Group.objects.get_or_create(name=HR_ADMIN)[0])
        self.client.login(username='hr', password='pw')
        self.general = Job.objects.create(job_code='GA00000', title=GENERAL_APPLICATION)
        self.role_a = Job.objects.create(job_code='A1', title='Marketing Associate')
        self.role_b = Job.objects.create(job_code='B1', title='Sales Associate')

    def test_general_application_is_always_sorted_last(self):
        response = self.client.get(reverse('job_manage_list'))
        titles = [j.title for j in response.context['jobs']]
        self.assertEqual(titles[-1], GENERAL_APPLICATION)
        self.assertEqual(set(titles[:-1]), {'Marketing Associate', 'Sales Associate'})

    def test_vacancy_count_excludes_general_application(self):
        response = self.client.get(reverse('job_manage_list'))
        self.assertEqual(response.context['total_vacancies'], 2)


class JobFormScoringCriteriaTests(TestCase):
    """Extra Scoring Criteria on the Create/Edit Vacancy form - a
    convenience for setting candidates.models.ScoringCriteria right when the
    vacancy is created, without a trip to the dedicated Scoring Criteria
    page. HR_ADMIN and Recruiter (the same two roles who can reach this form
    at all) - see jobs/views.py's _can_set_scoring_criteria. The dedicated
    Scoring Criteria page itself stays HR_ADMIN-only, unaffected."""

    def _post_data(self, **overrides):
        data = {
            'title': 'HR Business Partner', 'openings': 1, 'status': Job.Status.OPEN,
            'job_code': '', 'location': '', 'description': '', 'requirements': '',
            'opening_date': '', 'closing_date': '',
        }
        data.update(overrides)
        return data

    def setUp(self):
        self.admin = get_user_model().objects.create_user('admin', password='pw')
        self.admin.groups.add(Group.objects.get_or_create(name=HR_ADMIN)[0])
        self.recruiter = get_user_model().objects.create_user('recruiter', password='pw')
        self.recruiter.groups.add(Group.objects.get_or_create(name=RECRUITER)[0])

    def test_admin_sets_criteria_when_creating_a_vacancy(self):
        self.client.login(username='admin', password='pw')
        response = self.client.post(reverse('job_add'), self._post_data(
            extra_scoring_criteria='Weight AI/ML project experience heavily.'))
        self.assertEqual(response.status_code, 302)
        job = Job.objects.get(title='HR Business Partner')
        self.assertEqual(job.scoring_criteria.extra_instructions, 'Weight AI/ML project experience heavily.')
        self.assertEqual(job.scoring_criteria.updated_by, self.admin)

    def test_admin_sees_the_field_pre_filled_when_editing(self):
        job = Job.objects.create(title='Analyst')
        ScoringCriteria.objects.create(job=job, extra_instructions='Prefer IT industry background.')
        self.client.login(username='admin', password='pw')
        response = self.client.get(reverse('job_edit', args=[job.pk]))
        self.assertContains(response, 'Prefer IT industry background.')

    def test_admin_can_update_existing_criteria(self):
        job = Job.objects.create(title='Analyst')
        ScoringCriteria.objects.create(job=job, extra_instructions='Old criteria.')
        self.client.login(username='admin', password='pw')
        response = self.client.post(reverse('job_edit', args=[job.pk]), self._post_data(
            title='Analyst', extra_scoring_criteria='New criteria.'))
        self.assertEqual(response.status_code, 302)
        job.refresh_from_db()
        self.assertEqual(job.scoring_criteria.extra_instructions, 'New criteria.')

    def test_recruiter_sees_the_field_and_can_set_it(self):
        self.client.login(username='recruiter', password='pw')
        response = self.client.get(reverse('job_add'))
        self.assertContains(response, 'Extra Requirements')

        response = self.client.post(reverse('job_add'), self._post_data(
            extra_scoring_criteria='Prefer candidates with call-centre experience.'))
        self.assertEqual(response.status_code, 302)
        job = Job.objects.get(title='HR Business Partner')
        self.assertEqual(job.scoring_criteria.extra_instructions, 'Prefer candidates with call-centre experience.')
        self.assertEqual(job.scoring_criteria.updated_by, self.recruiter)

    def test_a_role_with_neither_permission_cannot_set_it(self):
        """Only HR_ADMIN/Recruiter can even reach this form - allowed_groups
        on JobCreateView/JobUpdateView already blocks anyone else with a
        403, so this just confirms _can_set_scoring_criteria agrees with
        that gate rather than trusting it blindly."""
        from jobs.views import _can_set_scoring_criteria
        outsider = get_user_model().objects.create_user('outsider', password='pw')
        self.assertFalse(_can_set_scoring_criteria(outsider))
