"""Tests for Bulk Upload CV: the Logic App client, the field mapping and the
upload -> parse -> candidate pipeline.

Run against sqlite so the live Azure DB is never touched:
    DB_ENGINE=sqlite python manage.py test candidates
"""
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from jobs.models import Job

from . import bulk, cv_parser, services
from .cv_parser import CVParseError
from .models import BulkUploadBatch, BulkUploadItem, Candidate, EmailRegistry
from .permissions import HIRING_MANAGER, RECRUITER

PARSED = {
    'status': 'ok',
    'Name': 'Asha Menon',
    'Email': 'Asha.Menon@example.com',
    'Mobile': '+91-9876543210',
    'Education': 'MBA - DC School of Management & Technology - 2019',
    'Role_Applied': 'Data Analyst',
    'Source': 'Careers',
    'Summary': 'Five years in analytics.',
    'CV_Link': 'https://sharepoint.example/cv.pdf',
}


class SplitEducationTests(TestCase):
    """Must match the parse in sql/sp_intake_add_candidate.sql so bulk rows and
    careers-intake rows produce identical education records."""

    def test_degree_college_year(self):
        self.assertEqual(
            cv_parser.split_education('MBA - DC School of Management & Technology - 2019'),
            ('MBA', 'DC School of Management & Technology', 2019))

    def test_degree_and_college_without_year(self):
        self.assertEqual(cv_parser.split_education('B.Tech - NIT Calicut'),
                         ('B.Tech', 'NIT Calicut', None))

    def test_degree_only(self):
        self.assertEqual(cv_parser.split_education('B.Sc Physics'),
                         ('B.Sc Physics', None, None))

    def test_trailing_number_that_is_not_a_year_stays_in_the_name(self):
        self.assertEqual(cv_parser.split_education('MBA - College - 19'),
                         ('MBA', 'College - 19', None))

    def test_blank(self):
        self.assertEqual(cv_parser.split_education(''), (None, None, None))


class MapFieldsTests(TestCase):
    def test_maps_and_cleans(self):
        fields, warning = cv_parser.map_to_candidate_fields(PARSED)
        self.assertIsNone(warning)
        self.assertEqual(fields['full_name'], 'Asha Menon')
        self.assertEqual(fields['email'], 'asha.menon@example.com')
        self.assertEqual(fields['phone'], '+91-9876543210')
        self.assertEqual(fields['resume_url'], 'https://sharepoint.example/cv.pdf')

    def test_missing_email_warns_instead_of_failing(self):
        fields, warning = cv_parser.map_to_candidate_fields({**PARSED, 'Email': 'N/A'})
        self.assertEqual(fields['email'], '')
        self.assertIn('email', warning.lower())

    def test_missing_sharepoint_link_is_flagged(self):
        _, warning = cv_parser.map_to_candidate_fields({**PARSED, 'CV_Link': ''})
        self.assertIn('SharePoint', warning)

    @override_settings(CV_PARSER_UPLOAD_TO_SHAREPOINT=False)
    def test_missing_link_is_not_flagged_when_upload_is_off(self):
        _, warning = cv_parser.map_to_candidate_fields({**PARSED, 'CV_Link': ''})
        self.assertIsNone(warning)

    def test_several_problems_are_reported_together(self):
        _, warning = cv_parser.map_to_candidate_fields(
            {**PARSED, 'Email': '', 'Summary': ''})
        self.assertIn('email', warning.lower())
        self.assertIn('summary', warning.lower())
        self.assertLessEqual(len(warning), 255)

    def test_missing_name_falls_back_to_filename(self):
        fields, warning = cv_parser.map_to_candidate_fields(
            {**PARSED, 'Name': ''}, fallback_name='Asha Menon Cv')
        self.assertEqual(fields['full_name'], 'Asha Menon Cv')
        self.assertIn('name', warning.lower())


@override_settings(LOGIC_APP_CV_PARSER_URL='https://logic.example/invoke')
class ParseCVTests(TestCase):
    def _response(self, status_code=200, json_body=None, headers=None, text=''):
        response = mock.Mock(status_code=status_code, headers=headers or {}, text=text)
        response.json.return_value = json_body if json_body is not None else {}
        return response

    def test_returns_parsed_body(self):
        with mock.patch('candidates.cv_parser.requests.post',
                        return_value=self._response(json_body=PARSED)) as post:
            data = cv_parser.parse_cv('cv.pdf', b'bytes', role_hint='Data Analyst')
        self.assertEqual(data['Email'], 'Asha.Menon@example.com')
        payload = post.call_args.kwargs['json']
        self.assertEqual(payload['filename'], 'cv.pdf')
        self.assertEqual(payload['role_hint'], 'Data Analyst')

    def test_workflow_error_becomes_readable_message(self):
        body = {'status': 'error', 'action': 'AnalyzeCV', 'message': 'Unsupported file.'}
        with mock.patch('candidates.cv_parser.requests.post',
                        return_value=self._response(json_body=body)):
            with self.assertRaises(CVParseError) as ctx:
                cv_parser.parse_cv('cv.pdf', b'bytes')
        self.assertIn('Unsupported file.', str(ctx.exception))
        self.assertIn('AnalyzeCV', str(ctx.exception))

    def test_http_error_is_reported(self):
        with mock.patch('candidates.cv_parser.requests.post',
                        return_value=self._response(status_code=502, text='Bad gateway')):
            with self.assertRaises(CVParseError) as ctx:
                cv_parser.parse_cv('cv.pdf', b'bytes')
        self.assertIn('502', str(ctx.exception))

    def test_202_is_polled_until_the_run_finishes(self):
        accepted = self._response(status_code=202, headers={'Location': 'https://logic.example/status'})
        with mock.patch('candidates.cv_parser.requests.post', return_value=accepted), \
             mock.patch('candidates.cv_parser.requests.get',
                        return_value=self._response(json_body=PARSED)) as get, \
             mock.patch('candidates.cv_parser.time.sleep'):
            data = cv_parser.parse_cv('cv.pdf', b'bytes')
        self.assertEqual(data['Name'], 'Asha Menon')
        get.assert_called_once()

    @override_settings(LOGIC_APP_CV_PARSER_URL='')
    def test_unconfigured_is_a_clear_error(self):
        with self.assertRaises(CVParseError) as ctx:
            cv_parser.parse_cv('cv.pdf', b'bytes')
        self.assertIn('not configured', str(ctx.exception))


class BulkProcessingTests(TestCase):
    def setUp(self):
        self.job = Job.objects.create(title='Data Analyst')
        self.batch = BulkUploadBatch.objects.create(job=self.job, source='Naukri')
        self.item = BulkUploadItem.objects.create(
            batch=self.batch, filename='Asha_Menon_CV.pdf',
            cv_file=SimpleUploadedFile('Asha_Menon_CV.pdf', b'%PDF-1.4 fake'))

    def test_success_creates_candidate_with_parsed_fields(self):
        with mock.patch('candidates.bulk.cv_parser.parse_cv', return_value=PARSED):
            bulk.process_item(self.item)

        self.item.refresh_from_db()
        self.assertEqual(self.item.status, BulkUploadItem.Status.SUCCESS)
        candidate = self.item.candidate
        self.assertEqual(candidate.email, 'asha.menon@example.com')
        self.assertEqual(candidate.cv_summary, 'Five years in analytics.')
        self.assertEqual(candidate.resume_url, 'https://sharepoint.example/cv.pdf')
        # Vacancy and source come from the upload screen, not from the CV.
        self.assertEqual(candidate.job, self.job)
        self.assertEqual(candidate.source, 'Naukri')
        self.assertEqual(candidate.status, Candidate.Status.OPEN)
        # Structured education record, same shape as the intake proc builds.
        education = candidate.education.get()
        self.assertEqual(education.qualification, 'MBA')
        self.assertEqual(education.institution, 'DC School of Management & Technology')
        self.assertEqual(education.year_completed, 2019)
        self.assertTrue(EmailRegistry.objects.filter(email='asha.menon@example.com').exists())
        self.assertTrue(candidate.resume_blob_url)

    def test_failure_creates_no_candidate(self):
        with mock.patch('candidates.bulk.cv_parser.parse_cv',
                        side_effect=CVParseError('Password-protected PDF.')):
            bulk.process_item(self.item)

        self.item.refresh_from_db()
        self.assertEqual(self.item.status, BulkUploadItem.Status.ERROR)
        self.assertIn('Password-protected', self.item.error_message)
        self.assertIsNone(self.item.candidate)
        self.assertEqual(Candidate.objects.count(), 0)

    def test_unexpected_error_is_contained(self):
        with mock.patch('candidates.bulk.cv_parser.parse_cv', side_effect=ValueError('boom')):
            bulk.process_item(self.item)
        self.item.refresh_from_db()
        self.assertEqual(self.item.status, BulkUploadItem.Status.ERROR)
        self.assertEqual(Candidate.objects.count(), 0)

    def test_missing_email_still_creates_a_flagged_candidate(self):
        with mock.patch('candidates.bulk.cv_parser.parse_cv',
                        return_value={**PARSED, 'Email': ''}):
            bulk.process_item(self.item)

        self.item.refresh_from_db()
        self.assertEqual(self.item.status, BulkUploadItem.Status.SUCCESS)
        self.assertTrue(self.item.warning)
        self.assertTrue(self.item.candidate.email_is_placeholder)
        # A placeholder address must never enter the duplicate registry.
        self.assertEqual(EmailRegistry.objects.count(), 0)

    def test_repeat_email_is_flagged_as_duplicate(self):
        first = Candidate.objects.create(full_name='Asha', email='asha.menon@example.com', job=self.job)
        services.register_application(first)

        with mock.patch('candidates.bulk.cv_parser.parse_cv', return_value=PARSED):
            bulk.process_item(self.item)

        self.item.refresh_from_db()
        self.assertTrue(self.item.candidate.is_duplicate)
        self.assertEqual(EmailRegistry.objects.get(email='asha.menon@example.com').application_count, 2)

    def test_an_already_claimed_item_is_not_parsed_twice(self):
        BulkUploadItem.objects.filter(pk=self.item.pk).update(status=BulkUploadItem.Status.PARSING)
        with mock.patch('candidates.bulk.cv_parser.parse_cv', return_value=PARSED) as parse:
            self.assertIsNone(bulk.process_item(self.item))
        parse.assert_not_called()
        self.assertEqual(Candidate.objects.count(), 0)

    def test_summarise_counts(self):
        BulkUploadItem.objects.create(
            batch=self.batch, filename='b.pdf',
            cv_file=SimpleUploadedFile('b.pdf', b'x'), status=BulkUploadItem.Status.SUCCESS)
        BulkUploadItem.objects.create(
            batch=self.batch, filename='c.pdf',
            cv_file=SimpleUploadedFile('c.pdf', b'x'), status=BulkUploadItem.Status.ERROR)
        _, counts = bulk.summarise(self.batch)
        self.assertEqual(counts, {'total': 3, 'success': 1, 'error': 1, 'waiting': 1,
                                  'done': 2, 'finished': False})


class BackButtonTests(TestCase):
    """Back from a candidate's own pages goes to the repository tab the
    candidate is in, not back through browser history."""

    def setUp(self):
        self.job = Job.objects.create(title='Data Analyst')
        self.user = get_user_model().objects.create_superuser('hr', 'hr@example.com', 'pw')
        self.client.force_login(self.user)
        self.candidate = Candidate.objects.create(
            full_name='Asha Menon', email='asha@example.com', job=self.job,
            status=Candidate.Status.SHORTLISTED)

    def test_timeline_back_points_at_the_candidates_tab(self):
        response = self.client.get(reverse('candidate_timeline', args=[self.candidate.pk]))
        self.assertEqual(response.context['back_url'],
                         f"{reverse('candidate_repository')}?tab=shortlisted")
        self.assertContains(response, 'Back to Candidates')
        # A multi-line {# #} comment renders as visible text - never ship one.
        self.assertNotContains(response, '{#')

    def test_edit_page_back_points_at_the_candidates_tab(self):
        response = self.client.get(reverse('candidate_edit', args=[self.candidate.pk]))
        self.assertEqual(response.context['back_url'],
                         f"{reverse('candidate_repository')}?tab=shortlisted")

    def test_back_follows_the_candidates_current_status(self):
        self.candidate.status = Candidate.Status.HIRED
        self.candidate.save(update_fields=['status'])
        response = self.client.get(reverse('candidate_timeline', args=[self.candidate.pk]))
        self.assertEqual(response.context['back_url'],
                         f"{reverse('candidate_repository')}?tab=hired")

    def test_pages_without_an_obvious_parent_keep_the_history_button(self):
        response = self.client.get(reverse('candidate_repository'))
        self.assertIsNone(response.context.get('back_url'))
        self.assertContains(response, 'goBack()')


class ListReturnTests(TestCase):
    """Acting on a candidate returns to the list the user was working in,
    filters and all - not a bare repository page."""

    def setUp(self):
        self.job = Job.objects.create(title='Data Analyst')
        self.user = get_user_model().objects.create_superuser('hr', 'hr@example.com', 'pw')
        self.client.force_login(self.user)
        self.candidate = Candidate.objects.create(
            full_name='Asha Menon', email='asha@example.com', job=self.job,
            status=Candidate.Status.SHORTLISTED)
        self.list_url = f"{reverse('candidate_repository')}?tab=shortlisted&q=asha"

    def test_back_returns_to_the_filtered_list(self):
        self.client.get(self.list_url)
        response = self.client.get(reverse('candidate_timeline', args=[self.candidate.pk]))
        self.assertEqual(response.context['back_url'], self.list_url)

    def test_delete_from_the_candidate_page_returns_to_that_list(self):
        self.client.get(self.list_url)
        response = self.client.post(
            reverse('candidate_delete', args=[self.candidate.pk]),
            {'next': reverse('candidate_timeline', args=[self.candidate.pk])})
        self.assertRedirects(response, self.list_url, fetch_redirect_response=False)
        self.assertFalse(Candidate.objects.filter(pk=self.candidate.pk).exists())

    def test_falls_back_to_the_repository_without_a_remembered_list(self):
        response = self.client.post(reverse('candidate_delete', args=[self.candidate.pk]), {})
        self.assertRedirects(response, reverse('candidate_repository'),
                             fetch_redirect_response=False)


class BulkDeleteTests(TestCase):
    def setUp(self):
        self.job = Job.objects.create(title='Data Analyst')
        self.user = get_user_model().objects.create_superuser('hr', 'hr@example.com', 'pw')
        self.client.force_login(self.user)
        self.candidates = [
            Candidate.objects.create(full_name=f'C{i}', email=f'c{i}@example.com', job=self.job)
            for i in range(3)]

    def test_deletes_only_the_selected_candidates(self):
        keep = self.candidates[2]
        response = self.client.post(reverse('candidate_bulk_delete'), {
            'ids': [self.candidates[0].pk, self.candidates[1].pk],
            'next': reverse('candidate_repository'),
        })
        self.assertRedirects(response, reverse('candidate_repository'),
                             fetch_redirect_response=False)
        self.assertEqual([c.pk for c in Candidate.objects.all()], [keep.pk])

    def test_nothing_selected_is_harmless(self):
        self.client.post(reverse('candidate_bulk_delete'), {'ids': []})
        self.assertEqual(Candidate.objects.count(), 3)

    def test_non_admins_cannot_bulk_delete(self):
        recruiter = get_user_model().objects.create_user('rec', 'rec@example.com', 'pw')
        recruiter.groups.add(Group.objects.get_or_create(name=RECRUITER)[0])
        self.client.force_login(recruiter)
        response = self.client.post(reverse('candidate_bulk_delete'),
                                    {'ids': [self.candidates[0].pk]})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(Candidate.objects.count(), 3)

    def test_repository_shows_tick_boxes_for_admins_only(self):
        response = self.client.get(reverse('candidate_repository'))
        self.assertContains(response, 'row-select')

        viewer = get_user_model().objects.create_user('view', 'view@example.com', 'pw')
        viewer.groups.add(Group.objects.get_or_create(name=HIRING_MANAGER)[0])
        self.client.force_login(viewer)
        response = self.client.get(reverse('candidate_repository'))
        self.assertNotContains(response, 'row-select')


class GeneralApplicationsTests(TestCase):
    def setUp(self):
        self.general = Job.objects.create(title='General Application')
        self.analyst = Job.objects.create(title='Data Analyst')
        self.user = get_user_model().objects.create_superuser('hr', 'hr@example.com', 'pw')
        self.client.force_login(self.user)
        self.designer = Candidate.objects.create(
            full_name='Dev One', email='dev1@example.com', job=self.general,
            role_applied='UI Designer')
        self.tester = Candidate.objects.create(
            full_name='Dev Two', email='dev2@example.com', job=self.general,
            role_applied='QA Tester')
        self.uncaptured = Candidate.objects.create(
            full_name='Dev Three', email='dev3@example.com', job=self.general)
        # Not a general application - must never appear.
        Candidate.objects.create(full_name='Ana Lyst', email='ana@example.com', job=self.analyst,
                                 role_applied='Data Analyst')
        self.url = reverse('candidate_general_applications')

    def test_lists_only_general_application_candidates(self):
        response = self.client.get(self.url)
        names = [c.full_name for c in response.context['candidates']]
        self.assertCountEqual(names, ['Dev One', 'Dev Two', 'Dev Three'])
        self.assertContains(response, 'UI Designer')

    def test_role_options_carry_counts_and_an_uncaptured_bucket(self):
        response = self.client.get(self.url)
        options = {o['role_applied']: o['n'] for o in response.context['role_options']}
        self.assertEqual(options, {'QA Tester': 1, 'UI Designer': 1})
        self.assertEqual(response.context['no_role_count'], 1)

    def test_filter_by_a_single_role(self):
        response = self.client.get(self.url, {'role': 'UI Designer'})
        self.assertEqual([c.pk for c in response.context['candidates']], [self.designer.pk])

    def test_filter_by_several_roles(self):
        response = self.client.get(self.url, {'role': ['UI Designer', 'QA Tester']})
        self.assertCountEqual([c.pk for c in response.context['candidates']],
                              [self.designer.pk, self.tester.pk])

    def test_filter_for_candidates_whose_role_was_never_captured(self):
        response = self.client.get(self.url, {'role': '__none__'})
        self.assertEqual([c.pk for c in response.context['candidates']], [self.uncaptured.pk])

    def test_search_matches_the_applied_position(self):
        response = self.client.get(self.url, {'q': 'designer'})
        self.assertEqual([c.pk for c in response.context['candidates']], [self.designer.pk])

    def test_careers_application_records_the_position_applied_for(self):
        from candidates import services
        candidate = Candidate(full_name='Web App', email='web@example.com', job=self.analyst)
        candidate.role_applied = self.analyst.title
        services.submit_application(candidate)
        self.assertEqual(Candidate.objects.get(pk=candidate.pk).role_applied, 'Data Analyst')


class BulkUploadViewTests(TestCase):
    def setUp(self):
        self.job = Job.objects.create(title='Data Analyst')
        self.user = get_user_model().objects.create_superuser('hr', 'hr@example.com', 'pw')
        self.client.force_login(self.user)

    def _upload(self, files):
        return self.client.post(reverse('candidate_bulk_upload'), {
            'job': self.job.pk, 'source': 'Naukri', 'cvs': files,
        })

    def test_upload_queues_items_and_starts_the_worker(self):
        files = [SimpleUploadedFile('a.pdf', b'%PDF a'), SimpleUploadedFile('b.docx', b'docx')]
        with mock.patch('candidates.views.bulk.start_batch') as start:
            response = self._upload(files)

        batch = BulkUploadBatch.objects.get()
        self.assertRedirects(response, reverse('candidate_bulk_progress', args=[batch.pk]))
        self.assertEqual(batch.items.count(), 2)
        self.assertEqual(batch.job, self.job)
        self.assertEqual(batch.source, 'Naukri')
        # Nothing enters the repository until a CV actually parses.
        self.assertEqual(Candidate.objects.count(), 0)
        start.assert_called_once()

    def test_rejects_unsupported_file_types(self):
        with mock.patch('candidates.views.bulk.start_batch'):
            response = self._upload([SimpleUploadedFile('notes.txt', b'hello')])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(BulkUploadBatch.objects.count(), 0)

    @override_settings(BULK_UPLOAD_MAX_MB=0)
    def test_rejects_oversized_files(self):
        with mock.patch('candidates.views.bulk.start_batch'):
            self._upload([SimpleUploadedFile('big.pdf', b'%PDF' * 100)])
        self.assertEqual(BulkUploadBatch.objects.count(), 0)

    def test_status_endpoint_reports_progress(self):
        batch = BulkUploadBatch.objects.create(job=self.job, source='Naukri')
        BulkUploadItem.objects.create(batch=batch, filename='a.pdf',
                                      cv_file=SimpleUploadedFile('a.pdf', b'x'),
                                      status=BulkUploadItem.Status.SUCCESS)
        response = self.client.get(reverse('candidate_bulk_status', args=[batch.pk]))
        self.assertEqual(response.json()['finished'], True)
        self.assertEqual(response.json()['success'], 1)

    def test_progress_page_renders_while_running_and_when_finished(self):
        batch = BulkUploadBatch.objects.create(job=self.job, source='Naukri')
        waiting = BulkUploadItem.objects.create(
            batch=batch, filename='a.pdf', cv_file=SimpleUploadedFile('a.pdf', b'x'))
        BulkUploadItem.objects.create(
            batch=batch, filename='b.pdf', cv_file=SimpleUploadedFile('b.pdf', b'x'),
            status=BulkUploadItem.Status.ERROR, error_message='Password-protected PDF.')
        url = reverse('candidate_bulk_progress', args=[batch.pk])

        running = self.client.get(url)
        self.assertEqual(running.status_code, 200)
        self.assertContains(running, 'bulk-bar')

        waiting.status = BulkUploadItem.Status.SUCCESS
        waiting.candidate = Candidate.objects.create(
            full_name='Asha Menon', email='asha.menon@example.com', job=self.job)
        waiting.save()

        finished = self.client.get(url)
        self.assertContains(finished, '1 CV uploaded successfully')
        self.assertContains(finished, '1 error')
        self.assertContains(finished, 'Password-protected PDF.')
        self.assertContains(finished, 'Retry 1 failed CV')

    def test_upload_page_lists_recent_batches(self):
        batch = BulkUploadBatch.objects.create(job=self.job, source='Naukri')
        BulkUploadItem.objects.create(batch=batch, filename='a.pdf',
                                      cv_file=SimpleUploadedFile('a.pdf', b'x'),
                                      status=BulkUploadItem.Status.SUCCESS)
        BulkUploadItem.objects.create(batch=batch, filename='b.pdf',
                                      cv_file=SimpleUploadedFile('b.pdf', b'x'),
                                      status=BulkUploadItem.Status.ERROR)

        response = self.client.get(reverse('candidate_bulk_upload'))
        self.assertContains(response, 'Recent Uploads')
        self.assertContains(response, '1 added')
        self.assertContains(response, '1 failed')
        self.assertContains(response, reverse('candidate_bulk_progress', args=[batch.pk]))

    def test_retry_requeues_failed_items(self):
        batch = BulkUploadBatch.objects.create(job=self.job, source='Naukri')
        item = BulkUploadItem.objects.create(
            batch=batch, filename='a.pdf', cv_file=SimpleUploadedFile('a.pdf', b'x'),
            status=BulkUploadItem.Status.ERROR, error_message='boom')
        with mock.patch('candidates.views.bulk.start_batch') as start:
            self.client.post(reverse('candidate_bulk_retry', args=[batch.pk]))
        item.refresh_from_db()
        self.assertEqual(item.status, BulkUploadItem.Status.PENDING)
        self.assertIsNone(item.error_message)
        start.assert_called_once()
