from unittest import mock

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from HR_management.template_text import render

from .match_scoring import SYSTEM_PROMPT as MATCH_SCORING_DEFAULT
from .models import PromptTemplate


class TemplateTextTests(TestCase):
    """HR_management/template_text.py's render() - shared by prompts and
    email_templates. Must never raise, even on a typo'd/removed placeholder,
    since one bad admin edit shouldn't turn into a 500 on every scoring call
    or every outbound email."""

    def test_fills_in_known_placeholders(self):
        self.assertEqual(render('Hello {name}!', name='World'), 'Hello World!')

    def test_leaves_unknown_placeholder_untouched(self):
        self.assertEqual(render('Hello {typo}!', name='World'), 'Hello {typo}!')

    def test_ignores_braces_with_no_word_inside(self):
        self.assertEqual(render('Use {} for empty.'), 'Use {} for empty.')


class PromptTemplateSeedTests(TestCase):
    """The seed migration (prompts/migrations/0002_seed_defaults.py) must
    produce rows whose text is byte-identical to what the code already
    shipped as SYSTEM_PROMPT - nothing should change in behaviour until an
    admin actually edits a row."""

    def test_all_five_prompts_are_seeded(self):
        keys = set(PromptTemplate.objects.values_list('key', flat=True))
        self.assertEqual(
            keys,
            {'match_scoring', 'cv_extraction', 'jd_extraction', 'profile_extraction', 'screening_questions'})

    def test_match_scoring_seed_matches_the_code_default(self):
        row = PromptTemplate.objects.get(key='match_scoring')
        self.assertEqual(row.text, MATCH_SCORING_DEFAULT)

    def test_screening_questions_seed_renders_identically_to_the_old_hardcoded_prompt(self):
        from candidates.screening_questions import QUESTION_COUNT
        from prompts.screening_questions import system_prompt as build_system_prompt

        row = PromptTemplate.objects.get(key='screening_questions')
        rendered = render(row.text, question_count=QUESTION_COUNT)
        self.assertEqual(rendered, build_system_prompt(QUESTION_COUNT))


class PromptTemplateValidationTests(TestCase):
    def test_match_scoring_requires_its_placeholders(self):
        row = PromptTemplate.objects.get(key='match_scoring')
        row.text = 'A prompt with no placeholders at all.'
        with self.assertRaises(ValidationError):
            row.full_clean()

    def test_screening_questions_requires_question_count(self):
        row = PromptTemplate.objects.get(key='screening_questions')
        row.text = 'Write some screening questions.'
        with self.assertRaises(ValidationError):
            row.full_clean()

    def test_cv_extraction_has_no_required_placeholders(self):
        row = PromptTemplate.objects.get(key='cv_extraction')
        row.text = 'Any wording at all is fine here.'
        row.full_clean()  # must not raise


class RenderPromptWiringTests(TestCase):
    """An admin edit must actually reach the Azure OpenAI call, not just sit
    in the database - verified the same way every existing AI-integration
    test in this codebase mocks its outbound requests.post()."""

    def _ok_response(self, content):
        return mock.Mock(status_code=200, text='', json=lambda: {
            'choices': [{'message': {'content': content}}]})

    def test_editing_match_scoring_prompt_changes_what_is_sent_to_azure_openai(self):
        from candidates import match_scoring
        from candidates.models import Candidate
        from jobs.models import Job

        row = PromptTemplate.objects.get(key='match_scoring')
        row.text = 'CUSTOM MARKER PROMPT. {must_have_block} {extra_criteria_block}'
        row.full_clean()
        row.save()

        job = Job.objects.create(job_code='J1', title='Recruiter')
        candidate = Candidate.objects.create(full_name='Jane Doe', email='jane@example.com', job=job)

        ok_json = '{"overall_score": 80, "likes": [], "not_matched": [], "rationale": "ok"}'
        with self.settings(AZURE_OPENAI_ENDPOINT='https://example.openai.azure.com', AZURE_OPENAI_KEY='k'):
            with mock.patch('candidates.match_scoring.requests.post',
                             return_value=self._ok_response(ok_json)) as post:
                match_scoring.score_candidate(candidate, job)
        payload = post.call_args.kwargs['json']
        system_message = payload['messages'][0]['content']
        self.assertIn('CUSTOM MARKER PROMPT.', system_message)


class PromptAdminTests(TestCase):
    def setUp(self):
        self.admin_user = get_user_model().objects.create_superuser('root', 'root@turnb.com', 'pw')
        self.client.force_login(self.admin_user)

    def test_add_is_disabled(self):
        response = self.client.get(reverse('admin:prompts_prompttemplate_add'))
        self.assertEqual(response.status_code, 403)

    def test_change_is_allowed(self):
        row = PromptTemplate.objects.get(key='cv_extraction')
        response = self.client.get(reverse('admin:prompts_prompttemplate_change', args=[row.pk]))
        self.assertEqual(response.status_code, 200)

    def test_saving_sets_updated_by(self):
        row = PromptTemplate.objects.get(key='cv_extraction')
        response = self.client.post(
            reverse('admin:prompts_prompttemplate_change', args=[row.pk]),
            {'text': row.text + '\n\nAn extra sentence.'})
        self.assertEqual(response.status_code, 302)
        row.refresh_from_db()
        self.assertIn('An extra sentence.', row.text)
        self.assertEqual(row.updated_by, self.admin_user)
