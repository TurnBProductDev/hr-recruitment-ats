"""Reject Reasons: required on every reject, shown and filterable, reported,
and suggested for old rejections.

    DB_ENGINE=sqlite python manage.py test candidates.test_reject_reasons
"""
import os
import tempfile

import openpyxl
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from jobs.models import Job

from . import services
from .models import Candidate, CandidateStatusHistory
from .permissions import HR_ADMIN
from .reject_reason_suggest import suggest

S = Candidate.Status


class _Base(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('hr', 'hr@example.com', 'pw')
        self.user.groups.add(Group.objects.get_or_create(name=HR_ADMIN)[0])
        self.client.force_login(self.user)
        self.job = Job.objects.create(job_code='SA1', title='Sales Associate')
        self.c = self._cand('Rose', 'rose@example.com')

    def _cand(self, name, email, status=S.SHORTLISTED):
        c = Candidate.objects.create(full_name=name, email=email, job=self.job)
        services.record_creation(c)
        if status != S.OPEN:
            services.change_status(c, status)
        return c

    def _last(self, c):
        return c.history.filter(new_status=S.REJECTED).latest('changed_at')


class RejectNeedsAReasonTests(_Base):
    def test_reject_without_a_reason_is_refused(self):
        self.client.post(reverse('candidate_reject', args=[self.c.pk]), {'reason': 'Remarks only'})
        self.c.refresh_from_db()
        self.assertEqual(self.c.status, S.SHORTLISTED)

    def test_reject_with_a_reason_and_no_remarks_works(self):
        self.client.post(reverse('candidate_reject', args=[self.c.pk]), {'reject_reason': 'CTC_MISMATCH'})
        self.c.refresh_from_db()
        self.assertEqual(self.c.status, S.REJECTED)
        row = self._last(self.c)
        self.assertEqual(row.reject_reason, 'CTC_MISMATCH')
        self.assertIsNone(row.remarks)

    def test_other_needs_the_typed_reason(self):
        self.client.post(reverse('candidate_reject', args=[self.c.pk]), {'reject_reason': 'OTHER'})
        self.c.refresh_from_db()
        self.assertEqual(self.c.status, S.SHORTLISTED)
        self.client.post(reverse('candidate_reject', args=[self.c.pk]),
                         {'reject_reason': 'OTHER', 'reject_reason_other': 'Visa issue', 'reason': 'Details'})
        row = self._last(self.c)
        self.assertEqual(row.reject_reason_label, 'Other: Visa issue')
        self.assertEqual(row.remarks, 'Details')

    def test_system_reason_cannot_be_picked_by_hr(self):
        self.client.post(reverse('candidate_reject', args=[self.c.pk]), {'reject_reason': 'DUPLICATE'})
        self.c.refresh_from_db()
        self.assertEqual(self.c.status, S.SHORTLISTED)

    def test_other_actions_still_need_remarks(self):
        self.client.post(reverse('candidate_round1', args=[self.c.pk]), {})
        self.c.refresh_from_db()
        self.assertEqual(self.c.status, S.SHORTLISTED)

    def test_bulk_reject_applies_the_reason_to_everyone(self):
        other = self._cand('Ann', 'ann@example.com')
        self.client.post(reverse('candidate_bulk_reject'),
                         {'ids': [self.c.pk, other.pk], 'reject_reason': 'POSITION_CLOSED'})
        for c in (self.c, other):
            self.assertEqual(self._last(c).reject_reason, 'POSITION_CLOSED')

    def test_bulk_reject_without_a_reason_rejects_nobody(self):
        self.client.post(reverse('candidate_bulk_reject'), {'ids': [self.c.pk], 'reason': 'x'})
        self.c.refresh_from_db()
        self.assertEqual(self.c.status, S.SHORTLISTED)

    def test_rejection_email_route_records_the_reason(self):
        self.client.post(reverse('candidate_send_rejection', args=[self.c.pk]),
                         {'skip_email': '1', 'reject_reason': 'WITHDREW'}, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(self._last(self.c).reject_reason, 'WITHDREW')

    def test_reason_is_only_stored_on_rejections(self):
        services.change_status(self.c, S.ROUND1, reject_reason='CTC_MISMATCH')
        self.assertIsNone(self.c.history.latest('changed_at').reject_reason)

    def test_candidate_page_offers_the_confirm_popup_and_reasons(self):
        response = self.client.get(reverse('candidate_timeline', args=[self.c.pk]))
        self.assertContains(response, 'id="decisionModal"')
        self.assertContains(response, 'Compensation Expectations (CTC) Mismatch')
        self.assertNotContains(response, 'Duplicate Application')  # system-only
        self.assertContains(response, 'data-decision="reject"')

    def test_candidate_page_shows_reason_in_history(self):
        services.change_status(self.c, S.REJECTED, remarks='Asked for 2x.', reject_reason='CTC_MISMATCH')
        response = self.client.get(reverse('candidate_timeline', args=[self.c.pk]))
        self.assertContains(response, 'Reason: Compensation Expectations (CTC) Mismatch')
        self.assertContains(response, 'Asked for 2x.')


class RejectedTabTests(_Base):
    def test_filter_by_reason_and_not_recorded(self):
        other = self._cand('Ann', 'ann@example.com')
        services.change_status(self.c, S.REJECTED, reject_reason='TECHNICAL_GAP')
        services.change_status(other, S.REJECTED, remarks='old style')
        url = reverse('candidate_repository')
        tech = self.client.get(url, {'tab': 'rejected', 'scoped': '1', 'reject_reason': 'TECHNICAL_GAP'})
        self.assertEqual([c.pk for c in tech.context['candidates']], [self.c.pk])
        none = self.client.get(url, {'tab': 'rejected', 'scoped': '1', 'reject_reason': '__none__'})
        self.assertEqual([c.pk for c in none.context['candidates']], [other.pk])
        self.assertContains(none, 'Not recorded')


class RejectionsReportTests(_Base):
    def test_totals_by_stage_and_reason(self):
        r1 = self._cand('Ann', 'ann@example.com', status=S.ROUND1)
        old = self._cand('Bob', 'bob@example.com')
        services.change_status(self.c, S.REJECTED, reject_reason='CTC_MISMATCH')
        services.change_status(r1, S.REJECTED, reject_reason='CTC_MISMATCH')
        services.change_status(old, S.REJECTED)
        response = self.client.get(reverse('hr_reports'), {'view': 'rejections'})
        ctx = response.context
        self.assertEqual(ctx['rej_total'], 3)
        self.assertEqual(ctx['rej_recorded'], 2)
        stages = {s['name']: s['count'] for s in ctx['rej_by_stage']}
        self.assertEqual(stages, {'Tele Screening': 2, 'Round 1': 1})
        reasons = {r['name']: r['count'] for r in ctx['rej_by_reason']}
        self.assertEqual(reasons, {'Compensation Expectations (CTC) Mismatch': 2, 'Not recorded': 1})
        self.assertEqual(ctx['rej_top_reason']['name'], 'Compensation Expectations (CTC) Mismatch')
        self.assertContains(response, 'id="rejectionsPane"')

    def test_vacancy_filter_applies(self):
        other_job = Job.objects.create(job_code='AN1', title='Analyst')
        x = Candidate.objects.create(full_name='X', email='x@example.com', job=other_job)
        services.change_status(x, S.REJECTED, reject_reason='WITHDREW')
        services.change_status(self.c, S.REJECTED, reject_reason='CTC_MISMATCH')
        ctx = self.client.get(reverse('hr_reports'), {'view': 'rejections', 'job': self.job.pk,
                                                      'setfilter': '1'}).context
        self.assertEqual(ctx['rej_total'], 1)


class SuggestionTests(_Base):
    def test_keyword_rules(self):
        cases = {
            'Expected CTC is too high': 'CTC_MISMATCH',
            'Poor English communication': 'COMMUNICATION',
            'Not interested in the role': 'WITHDREW',
            'Weak SQL fundamentals': 'TECHNICAL_GAP',
            'Position filled': 'POSITION_CLOSED',
            'Notice period 90 days': 'NOTICE_PERIOD',
            'Not willing to relocate to Kochi': 'RELOCATION',
            'Round 1 interview failed.': 'INTERVIEW_PERFORMANCE',
            'Auto-rejected: duplicate application - ID1 for the same vacancy is already in process.': 'DUPLICATE',
            'Profile not a fit': 'ROLE_FIT',
            'xyz': 'OTHER',
            '': 'OTHER',
        }
        for remarks, code in cases.items():
            self.assertEqual(suggest(remarks), code, remarks)

    def test_export_then_apply_fills_only_missing_reasons(self):
        a = self._cand('Ann', 'ann@example.com')
        services.change_status(self.c, S.REJECTED, remarks='Salary expectation too high')
        services.change_status(a, S.REJECTED, remarks='Picked by HR', reject_reason='WITHDREW')
        path = os.path.join(tempfile.mkdtemp(), 'review.xlsx')
        call_command('suggest_reject_reasons', export=path, stdout=open(os.devnull, 'w'))
        wb = openpyxl.load_workbook(path)
        rows = list(wb['Review'].iter_rows(values_only=True))
        self.assertEqual(len(rows), 2)  # header + Rose only (Ann already has a reason)
        self.assertEqual(rows[1][7], 'Compensation Expectations (CTC) Mismatch')
        wb['Review'].cell(row=2, column=9, value='Role Fit Mismatch')  # HR overrides the suggestion
        wb.save(path)
        call_command('suggest_reject_reasons', apply=path, stdout=open(os.devnull, 'w'))
        row = CandidateStatusHistory.objects.filter(candidate=self.c, new_status=S.REJECTED).get()
        self.assertEqual(row.reject_reason, 'ROLE_FIT')
        self.assertEqual(row.remarks, 'Salary expectation too high')  # untouched
        self.assertEqual(self._last(a).reject_reason, 'WITHDREW')
