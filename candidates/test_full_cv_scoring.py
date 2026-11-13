"""Score Candidates: full CV text, HR's score bands and factor ratings.

Run against sqlite so the live Azure DB is never touched:
    DB_ENGINE=sqlite python manage.py test candidates.test_full_cv_scoring
"""
import json
import shutil
import tempfile
from unittest import mock

from django.core.cache import cache
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings

from jobs.models import Job

from . import cv_text, match_scoring
from .models import Candidate
from .templatetags.candidates_extras import score_band, score_factors


def _pdf(text):
    import pymupdf
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), text)
    data = doc.tobytes()
    doc.close()
    return data


class ScoreBandTests(TestCase):
    def test_bands_match_hr_cutoffs(self):
        cases = {100: 'Strong Fit', 85: 'Strong Fit', 84: 'Strong Potential - Minor Gaps',
                 70: 'Strong Potential - Minor Gaps', 69: 'Potential - Significant Gaps',
                 55: 'Potential - Significant Gaps', 54: 'Consider',
                 40: 'Consider', 39: 'Unfit', 0: 'Unfit', None: ''}
        for score, label in cases.items():
            self.assertEqual(score_band(score), label, score)

    def test_factors_listed_in_display_order_and_empty_for_old_scores(self):
        self.assertEqual(score_factors({'factors': {'seniority_fit': 7, 'core_function_experience': 6}}),
                         [('Core experience', 6), ('Seniority', 7)])
        self.assertEqual(score_factors({'score': 65}), [])


class FullCvTextTests(TestCase):
    def setUp(self):
        self.media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media, ignore_errors=True)
        override = override_settings(MEDIA_ROOT=self.media)
        override.enable()
        self.addCleanup(override.disable)
        self.job = Job.objects.create(job_code='S1', title='Sales Associate')
        self.candidate = Candidate.objects.create(full_name='A', email='a@example.com', job=self.job)

    def test_reads_uploaded_cv_once_and_rereads_when_file_changes(self):
        self.candidate.resume_blob_url.save('cv.pdf', ContentFile(_pdf('Sales Officer at Philips')))
        self.assertIn('Sales Officer at Philips', cv_text.get_cv_text(self.candidate))
        self.candidate.refresh_from_db()
        self.assertIn('Philips', self.candidate.cv_text)          # stored for next time

        with mock.patch.object(cv_text, '_extract') as extract:
            cv_text.get_cv_text(self.candidate)                      # same file: no re-read
            extract.assert_not_called()

        self.candidate.resume_blob_url.save('cv2.pdf', ContentFile(_pdf('Deputy Manager at HDFC')))
        self.assertIn('HDFC', cv_text.get_cv_text(self.candidate))

    def test_no_readable_cv_returns_none(self):
        self.candidate.resume_url = 'https://example.sharepoint.com/:b:/s/TeamHR/abc'
        self.assertIsNone(cv_text.get_cv_text(self.candidate))


@override_settings(AZURE_OPENAI_ENDPOINT='https://x.openai.azure.com', AZURE_OPENAI_KEY='k')
class ScoringUsesFullCvTests(TestCase):
    def setUp(self):
        cache.clear()
        self.job = Job.objects.create(job_code='S1', title='Sales Associate')
        self.candidate = Candidate.objects.create(full_name='A', email='a@example.com', job=self.job)

    def _response(self, core=6, overall=72):
        body = {'factor_ratings': {'core_function_experience': core, 'domain_relevance': 5, 'seniority_fit': 7,
                                   'evidence_of_results': 7, 'education_and_skills': 8},
                'likes': ['x'], 'not_matched': ['y'], 'overall_score': overall, 'rationale': 'r'}
        resp = mock.Mock(status_code=200)
        resp.json.return_value = {'choices': [{'message': {'content': json.dumps(body)}}]}
        return resp

    def test_full_cv_sent_and_factor_ratings_kept(self):
        with mock.patch.object(cv_text, 'get_cv_text', return_value='WHOLE RESUME TEXT'), \
             mock.patch('candidates.match_scoring.requests.post', return_value=self._response()) as post:
            result = match_scoring.score_candidate(self.candidate, self.job)
        user_msg = post.call_args.kwargs['json']['messages'][1]['content']
        self.assertIn('FULL CV TEXT:\nWHOLE RESUME TEXT', user_msg)
        self.assertEqual(result['score'], 72)
        self.assertEqual(result['factors']['core_function_experience'], 6)

    def test_missing_cv_is_flagged_to_the_model(self):
        with mock.patch.object(cv_text, 'get_cv_text', return_value=None), \
             mock.patch('candidates.match_scoring.requests.post', return_value=self._response()) as post:
            match_scoring.score_candidate(self.candidate, self.job)
        self.assertIn('Full CV text not available', post.call_args.kwargs['json']['messages'][1]['content'])

    def test_no_core_experience_is_capped_at_consider(self):
        with mock.patch.object(cv_text, 'get_cv_text', return_value=None),              mock.patch('candidates.match_scoring.requests.post', return_value=self._response(core=3, overall=61)):
            result = match_scoring.score_candidate(self.candidate, self.job)
        self.assertEqual(result['score'], 54)
