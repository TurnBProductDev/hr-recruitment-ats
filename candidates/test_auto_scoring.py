"""New applications are scored automatically (candidates/duplicate_rule.py ->
candidates/scoring.start_scoring).

    DB_ENGINE=sqlite python manage.py test candidates.test_auto_scoring
"""
from datetime import timedelta
from unittest import mock

from django.test import TestCase
from django.utils import timezone

from jobs.models import Job

from . import duplicate_rule, scoring, services
from .models import Candidate, DuplicateRuleState

S = Candidate.Status
MS = Candidate.MatchState


@mock.patch('candidates.match_scoring.is_configured', return_value=True)
@mock.patch('candidates.scoring.start_scoring')
class AutoScoringTests(TestCase):
    def setUp(self):
        self.job = Job.objects.create(title='HRBP', job_code='HR1', status=Job.Status.OPEN)
        self.general = Job.objects.create(title='General Application', job_code='GA1', status=Job.Status.OPEN)
        DuplicateRuleState.objects.all().delete()
        duplicate_rule.run_pending()  # seeds the mark at "now"

    def _new(self, email='a@example.com', job=None, **kw):
        return Candidate.objects.create(full_name='A', email=email, job=job or self.job, **kw)

    def _sweep(self):
        with self.captureOnCommitCallbacks(execute=True):
            duplicate_rule.run_pending()

    def test_new_open_application_on_a_vacancy_is_scored(self, start, _configured):
        new = self._new()
        self._sweep()
        start.assert_called_once_with([new.pk])

    def test_mailbox_style_batch_scores_each_eligible_once(self, start, _configured):
        a, b = self._new('a@example.com'), self._new('b@example.com')
        self._sweep()
        start.assert_called_once_with([a.pk, b.pk])
        start.reset_mock()
        self._sweep()  # already checked - nothing new
        start.assert_not_called()

    def test_general_application_is_not_scored(self, start, _configured):
        self._new(job=self.general)
        self._sweep()
        start.assert_not_called()

    def test_duplicate_rejected_application_is_not_scored(self, start, _configured):
        old = self._new()
        Candidate.objects.filter(pk=old.pk).update(status=S.REJECTED, created_at=timezone.now() - timedelta(days=30))
        DuplicateRuleState.objects.update(last_candidate_id=old.pk)
        new = self._new()
        self._sweep()
        new.refresh_from_db()
        self.assertEqual(new.status, S.REJECTED)
        start.assert_not_called()

    def test_earlier_open_one_superseded_in_the_same_batch_is_not_scored(self, start, _configured):
        """Both arrive before the sweep: the later one rejects the earlier one,
        so only the later one is scored."""
        first = self._new()
        second = self._new()
        self._sweep()
        first.refresh_from_db()
        self.assertEqual(first.status, S.REJECTED)
        start.assert_called_once_with([second.pk])

    def test_blacklisted_email_is_not_scored(self, start, _configured):
        bad = self._new('bad@example.com')
        services.register_application(bad)
        services.blacklist_candidate(bad, 'Fake documents')
        DuplicateRuleState.objects.update(last_candidate_id=bad.pk)
        self._new('bad@example.com')
        self._sweep()
        start.assert_not_called()

    def test_already_scored_is_not_rescored(self, start, _configured):
        self._new(match_state=MS.DONE)
        self._sweep()
        start.assert_not_called()

    def test_nothing_queued_when_scoring_is_not_configured(self, start, configured):
        configured.return_value = False
        self._new()
        self._sweep()
        start.assert_not_called()

    def test_careers_form_route_is_scored(self, start, _configured):
        c = Candidate(full_name='Form', email='form@example.com', job=self.job)
        with self.captureOnCommitCallbacks(execute=True):
            services.submit_application(c)
        start.assert_called_once_with([c.pk])


class StartScoringTests(TestCase):
    def test_scores_each_candidate_once(self):
        job = Job.objects.create(title='HRBP', job_code='HR1', status=Job.Status.OPEN)
        a = Candidate.objects.create(full_name='A', email='a@example.com', job=job)
        with mock.patch('candidates.scoring.threading.Thread') as thread, \
                mock.patch('candidates.scoring.score_one') as score_one, \
                mock.patch('candidates.scoring.connection'):
            scoring.start_scoring([a.pk])
            target, args = thread.call_args.kwargs['target'], thread.call_args.kwargs['args']
            target(*args)  # run the worker inline
        score_one.assert_called_once()
        self.assertEqual(score_one.call_args.args[0].pk, a.pk)

    def test_empty_list_starts_nothing(self):
        with mock.patch('candidates.scoring.threading.Thread') as thread:
            self.assertIsNone(scoring.start_scoring([]))
        thread.assert_not_called()
