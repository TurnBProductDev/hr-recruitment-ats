"""Telling a CV from a cover letter / unrelated PDF, filing those as
attachments instead of candidates, and the cleanup_intake_rows command for
rows that came in before that existed.

Run against sqlite so the live Azure DB is never touched:
    DB_ENGINE=sqlite python manage.py test candidates.test_intake_documents
"""
import base64
import io
import json
import os
import tempfile
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import bulk, cv_extraction
from .models import (
    Attachment, BulkUploadBatch, BulkUploadItem, Candidate, CandidateEducation, CandidateStatusHistory,
    EmailRegistry,
)
from .tests import PROFILE


@override_settings(AZURE_OPENAI_ENDPOINT='https://example.openai.azure.com', AZURE_OPENAI_KEY='fake-key')
class DocumentTypeTests(TestCase):
    """prompts/cv_extraction.py rule 12 - only a CV ever becomes a candidate."""

    def _response(self, **overrides):
        body = {'document_type': 'cv', 'matched_job_title': None, 'name': 'Rose E G', 'email': 'rose@example.com',
                'mobile': None, 'dob': None, 'current_location': None, 'linkedin': None, 'portfolio_url': None,
                'qualification': None, 'last_role': None, 'last_company': None, 'total_experience_years': None,
                'skills': None, 'notice_period': None, 'expected_salary': None, 'current_salary': None,
                'role_applied': None, 'source': None, 'summary': None, 'experience': [], 'education': [],
                **overrides}
        response = mock.Mock(status_code=200, text='')
        response.json.return_value = {'choices': [{'message': {'content': json.dumps(body)}}]}
        return response

    def _extract(self, response, **kwargs):
        with mock.patch('candidates.cv_extraction.pdf_text.extract_text', return_value='some document text'), \
             mock.patch('candidates.cv_extraction.requests.post', return_value=response) as post:
            fields = cv_extraction.extract_profile(b'%PDF-1.4', **kwargs)
        return fields, post

    def test_document_type_is_required_on_the_schema(self):
        _, post = self._extract(self._response())
        schema = post.call_args.kwargs['json']['response_format']['json_schema']['schema']
        self.assertEqual(schema['properties']['document_type']['enum'], ['cv', 'cover_letter', 'other'])
        self.assertIn('document_type', schema['required'])

    def test_document_type_passes_through(self):
        fields, _ = self._extract(self._response(document_type='cover_letter'))
        self.assertEqual(fields['document_type'], 'cover_letter')

    def test_unexpected_document_type_counts_as_a_cv(self):
        # A model hiccup must never silently drop a real application.
        fields, _ = self._extract(self._response(document_type='brochure'))
        self.assertEqual(fields['document_type'], 'cv')

    def test_filename_reaches_the_model(self):
        _, post = self._extract(self._response(), filename='Cover Letter.pdf')
        user_message = post.call_args.kwargs['json']['messages'][1]['content']
        self.assertIn('File name: Cover Letter.pdf', json.dumps(user_message))

    @override_settings(CV_EXTRACT_API_KEY='test-key')
    def test_api_returns_document_type_and_passes_the_filename(self):
        with mock.patch('candidates.views.cv_extraction.extract_profile',
                        return_value={**PROFILE, 'document_type': 'other'}) as extract:
            response = self.client.post(
                reverse('cv_extract_api'), content_type='application/json', HTTP_X_API_KEY='test-key',
                data=json.dumps({'content_base64': base64.b64encode(b'%PDF-1.4').decode(),
                                 'filename': 'MBA Brochure.pdf'}))
        self.assertEqual(response.json()['document_type'], 'other')
        self.assertEqual(extract.call_args.kwargs['filename'], 'MBA Brochure.pdf')

    def test_bulk_upload_skips_a_non_cv(self):
        batch = BulkUploadBatch.objects.create(source='Naukri')
        item = BulkUploadItem.objects.create(
            batch=batch, filename='Cover Letter.pdf', cv_file=SimpleUploadedFile('Cover Letter.pdf', b'%PDF-1.4'))
        with mock.patch('candidates.bulk.cv_extraction.extract_profile',
                        return_value={**PROFILE, 'document_type': 'cover_letter'}) as extract:
            bulk.process_item(item)
        item.refresh_from_db()
        self.assertEqual(item.status, BulkUploadItem.Status.ERROR)
        self.assertIn('Not a CV', item.error_message)
        self.assertIn('cover letter', item.error_message)
        self.assertEqual(extract.call_args.kwargs['filename'], 'Cover Letter.pdf')
        self.assertEqual(Candidate.objects.count(), 0)


@override_settings(AZURE_STORAGE_ACCOUNT_NAME='hirebcvfiles', AZURE_STORAGE_ACCOUNT_KEY='a2V5')
class CandidateAttachmentViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser('hr', 'hr@example.com', 'pw')
        self.client.force_login(self.user)
        self.candidate = Candidate.objects.create(full_name='Rose E G', email='rose@example.com')

    def _get(self, attachment, candidate=None):
        return self.client.get(reverse('candidate_attachment', args=[(candidate or self.candidate).pk, attachment.pk]))

    def test_blob_link_is_signed(self):
        a = Attachment.objects.create(
            candidate=self.candidate,
            url='https://hirebcvfiles.blob.core.windows.net/candidate-cvs/x-Cover%20Letter.pdf')
        response = self._get(a)
        self.assertEqual(response.status_code, 302)
        self.assertIn('sig=', response['Location'])

    def test_external_link_redirects_as_is(self):
        a = Attachment.objects.create(candidate=self.candidate, url='https://sharepoint.example/cl.pdf')
        self.assertEqual(self._get(a)['Location'], 'https://sharepoint.example/cl.pdf')

    def test_another_candidates_attachment_is_not_found(self):
        other = Candidate.objects.create(full_name='Other', email='o@example.com')
        a = Attachment.objects.create(candidate=other, url='https://sharepoint.example/cl.pdf')
        self.assertEqual(self._get(a).status_code, 404)

    def test_timeline_lists_the_attachment(self):
        a = Attachment.objects.create(candidate=self.candidate, label='Cover letter - CL.pdf',
                                      url='https://sharepoint.example/cl.pdf')
        response = self.client.get(reverse('candidate_timeline', args=[self.candidate.pk]))
        self.assertContains(response, 'Cover letter - CL.pdf')
        self.assertContains(response, reverse('candidate_attachment', args=[self.candidate.pk, a.pk]))
        self.assertNotContains(response, 'No attachments.')


class CleanupIntakeRowsCommandTests(TestCase):
    """manage.py cleanup_intake_rows - review writes a plan and changes
    nothing; apply carries it out for the approved ids only."""

    def setUp(self):
        self.plan = os.path.join(tempfile.mkdtemp(), 'plan.json')
        self.applied = timezone.now()

    def _intake(self, name, email, url, education=True, **kw):
        c = Candidate.objects.create(full_name=name, email=email, resume_url=url, **kw)
        Candidate.objects.filter(pk=c.pk).update(created_at=self.applied)
        CandidateStatusHistory.objects.create(candidate=c, new_status='OPEN', performed_by='Careers Intake')
        if education:
            CandidateEducation.objects.create(candidate=c, qualification='MBA')
        return c

    def _review(self, types):
        with mock.patch('candidates.management.commands.cleanup_intake_rows._classify',
                        side_effect=lambda c: (types[c.pk], '')):
            call_command('cleanup_intake_rows', 'review', plan=self.plan, stdout=io.StringIO())
        with open(self.plan, encoding='utf-8') as fh:
            return {r['id']: r for r in json.load(fh)}

    def _apply(self, ids):
        call_command('cleanup_intake_rows', 'apply', plan=self.plan, ids=ids, stdout=io.StringIO())

    def test_split_email_cover_letter_is_attached_then_deleted(self):
        letter = self._intake('Rose E G', 'rose@example.com', 'https://x.example/cl.pdf')
        cv = self._intake('Rose E G', 'rose@example.com', 'https://x.example/cv.pdf', is_duplicate=True)
        EmailRegistry.objects.create(email='rose@example.com', application_count=2, first_candidate=letter)
        brochure = self._intake('MBA Brochure.pdf', 'pending-1@placeholder.local', 'https://x.example/b.pdf',
                                education=False)
        # A Bulk Upload row is never in scope, however it looks.
        not_intake = Candidate.objects.create(full_name='Bulk.pdf', email='pending-2@placeholder.local')

        plan = self._review({letter.pk: 'cover_letter', cv.pk: 'cv', brochure.pk: 'other'})
        self.assertEqual(plan[letter.pk]['action'], 'attach_then_delete')
        self.assertEqual(plan[letter.pk]['attach_to'], cv.pk)
        self.assertEqual(plan[cv.pk]['action'], 'keep')
        self.assertEqual(plan[brochure.pk]['action'], 'delete')
        self.assertNotIn(not_intake.pk, plan)
        self.assertEqual(Candidate.objects.count(), 4)  # review changes nothing

        self._apply(f'{letter.pk},{brochure.pk}')
        self.assertEqual(set(Candidate.objects.values_list('pk', flat=True)), {cv.pk, not_intake.pk})
        attachment = cv.attachments.get()
        self.assertEqual(attachment.url, 'https://x.example/cl.pdf')
        self.assertEqual(attachment.label, 'Cover letter - cl.pdf')
        entry = EmailRegistry.objects.get(email='rose@example.com')
        self.assertEqual((entry.application_count, entry.first_candidate_id), (1, cv.pk))
        cv.refresh_from_db()
        self.assertFalse(cv.is_duplicate)  # no longer shown as a reapply

    def test_same_cv_twice_in_one_email_keeps_one(self):
        first = self._intake('Rose E G', 'rose@example.com', 'https://x.example/cv.pdf')
        second = self._intake('Rose E G', 'rose@example.com', 'https://x.example/cv.docx', is_duplicate=True)
        plan = self._review({first.pk: 'cv', second.pk: 'cv'})
        self.assertEqual(plan[first.pk]['action'], 'keep')
        self.assertEqual(plan[second.pk]['action'], 'delete')

    def test_same_file_filed_twice_needs_no_reading(self):
        first = self._intake('Nidhin M', 'nidhin@example.com', 'https://sharepoint.example/same')
        copy = self._intake('Nidhin M', 'nidhin@example.com', 'https://sharepoint.example/same', is_duplicate=True)
        plan = self._review({})  # _classify must not be called - an empty map would KeyError
        self.assertEqual((plan[first.pk]['action'], plan[copy.pk]['action']), ('keep', 'delete'))
        self._apply(str(copy.pk))
        self.assertEqual(list(Candidate.objects.values_list('pk', flat=True)), [first.pk])
        self.assertFalse(first.attachments.exists())  # a copy of the CV isn't a separate document

    def test_attach_files_a_manual_row_onto_a_chosen_candidate(self):
        # Documents a candidate emailed in later (no CV in that email at all).
        real = Candidate.objects.create(full_name='Ansu Titus', email='ansu@example.com')
        letter = self._intake('Ansu Titus', 'ansu@example.com', 'https://x.example/relieving.pdf', is_duplicate=True)
        comp = self._intake('Ansu Titus', 'ansu@example.com', 'https://x.example/comp.pdf', is_duplicate=True)
        EmailRegistry.objects.create(email='ansu@example.com', application_count=3, first_candidate=real)
        plan = self._review({letter.pk: 'other', comp.pk: 'other'})
        self.assertEqual(plan[letter.pk]['action'], 'manual_check')

        call_command('cleanup_intake_rows', 'apply', plan=self.plan,
                     attach=f'{letter.pk}:{real.pk},{comp.pk}:{real.pk}', stdout=io.StringIO())
        self.assertEqual(list(Candidate.objects.values_list('pk', flat=True)), [real.pk])
        self.assertEqual(sorted(real.attachments.values_list('label', flat=True)),
                         ['Other document - comp.pdf', 'Other document - relieving.pdf'])
        self.assertEqual(EmailRegistry.objects.get(email='ansu@example.com').application_count, 1)

    def test_unreadable_file_is_left_for_a_manual_check(self):
        c = self._intake('Golam Israil Resume.pdf', 'pending-3@placeholder.local', 'https://x.example/r.pdf')
        self.assertEqual(self._review({c.pk: 'unknown'})[c.pk]['action'], 'manual_check')

    def test_apply_refuses_ids_not_proposed_for_removal(self):
        c = self._intake('Golam Israil Resume.pdf', 'pending-3@placeholder.local', 'https://x.example/r.pdf')
        self._review({c.pk: 'cv'})
        with self.assertRaises(CommandError):
            self._apply(str(c.pk))
        self.assertTrue(Candidate.objects.filter(pk=c.pk).exists())
