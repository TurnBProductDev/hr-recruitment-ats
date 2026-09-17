"""Dashboard summary tests.

Run against sqlite so the live Azure DB is never touched:
    DB_ENGINE=sqlite python manage.py test dashboard
"""
import re
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from candidates import services
from candidates.models import Candidate, CandidateStatusHistory, CommunicationLog
from candidates.permissions import HR_ADMIN, INTERVIEWER, RECRUITER
from candidates.views import GENERAL_APPLICATION
from interviews.models import Interview, InterviewReschedule
from jobs.models import Job

from . import daily_view
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

    def test_by_job_openings_is_not_multiplied_by_candidate_count(self):
        """`openings` is a per-job value, not per-candidate - annotating it
        with Sum() across a group with 9 candidates would wrongly multiply a
        job with 1 opening out to 9. Must use Max() instead."""
        row = self._row('by_job')
        self.assertEqual(row['total'], 9)
        self.assertEqual(row['openings'], 1)  # Job.openings default, not 9


class OpenVacanciesDefaultScopeTests(TestCase):
    """A fresh arrival at the dashboard (no query string at all) defaults to
    Open vacancies only - the 'scoped' hidden field only appears once the
    filter form has actually been submitted, so its absence is what marks
    "first visit" vs "the user explicitly unchecked the switch this
    request." See HRDashboardView.get_context_data."""

    def setUp(self):
        self.user = get_user_model().objects.create_superuser('hr3', 'hr3@example.com', 'pw')
        self.client.force_login(self.user)
        self.open_job = Job.objects.create(title='Open Role', status=Job.Status.OPEN)
        self.closed_job = Job.objects.create(title='Closed Role', status=Job.Status.CLOSED)
        Candidate.objects.create(full_name='Open Candidate', email='open@example.com', job=self.open_job)
        Candidate.objects.create(full_name='Closed Candidate', email='closed@example.com', job=self.closed_job)

    def test_fresh_visit_defaults_to_open_vacancies_only(self):
        response = self.client.get(reverse('hr_dashboard'))
        self.assertEqual(response.context['scope'], 'open')
        self.assertEqual(response.context['summary']['total'], 1)

    def test_explicitly_unchecking_shows_every_vacancy(self):
        response = self.client.get(f"{reverse('hr_dashboard')}?scoped=1")
        self.assertEqual(response.context['scope'], '')
        self.assertEqual(response.context['summary']['total'], 2)

    def test_explicitly_checking_is_still_respected(self):
        response = self.client.get(f"{reverse('hr_dashboard')}?scoped=1&scope=open")
        self.assertEqual(response.context['scope'], 'open')
        self.assertEqual(response.context['summary']['total'], 1)


class GeneralApplicationAndFutureProspectsExcludedTests(TestCase):
    """Neither General Application candidates nor Future Prospects (a hold
    taken before ever being screened) count toward any dashboard number -
    each gets its own separate, always-visible count instead (the top-right
    quick links on the Summary page)."""

    def setUp(self):
        self.user = get_user_model().objects.create_superuser('hr4', 'hr4@example.com', 'pw')
        self.client.force_login(self.user)
        self.job = Job.objects.create(title='Program Manager')
        self.general = Job.objects.create(title='General Application')
        Candidate.objects.create(full_name='Mapped', email='mapped@example.com', job=self.job)
        Candidate.objects.create(full_name='Unmapped', email='unmapped@example.com', job=self.general)
        Candidate.objects.create(
            full_name='Future Prospect', email='prospect@example.com', job=self.job,
            status=Candidate.Status.SCREENING_HOLD, hold_from_status=Candidate.Status.OPEN)

    def _get(self):
        return self.client.get(f"{reverse('hr_dashboard')}?scoped=1")  # scope off - see every vacancy

    def test_summary_total_excludes_both(self):
        response = self._get()
        self.assertEqual(response.context['summary']['total'], 1)  # just 'Mapped'

    def test_by_job_never_lists_general_application(self):
        response = self._get()
        titles = [row['job__title'] for row in response.context['by_job']]
        self.assertNotIn('General Application', titles)

    def test_side_counts_are_shown_regardless_of_filters(self):
        response = self._get()
        self.assertEqual(response.context['general_applications_count'], 1)
        self.assertEqual(response.context['future_prospects_count'], 1)
        self.assertContains(response, reverse('candidate_general_applications'))
        self.assertContains(response, reverse('candidate_future_prospects'))


class OpenPositionsTests(TestCase):
    """Open Positions sums Job.openings (individual positions to fill)
    across open, non-archived vacancies - not a count of vacancy postings,
    since one posting can cover more than one opening - and always leaves
    out General Application, even if its status were ever OPEN."""

    def setUp(self):
        self.user = get_user_model().objects.create_superuser('hr5', 'hr5@example.com', 'pw')
        self.client.force_login(self.user)

    def _get(self):
        return self.client.get(f"{reverse('hr_dashboard')}?scoped=1")

    def test_sums_openings_not_row_count(self):
        Job.objects.create(title='Analytics Consultant AI', status=Job.Status.OPEN, openings=2)
        Job.objects.create(title='Sales Associate', status=Job.Status.OPEN, openings=1)
        response = self._get()
        self.assertEqual(response.context['open_positions'], 3)

    def test_excludes_closed_and_archived(self):
        Job.objects.create(title='Closed Role', status=Job.Status.CLOSED, openings=5)
        Job.objects.create(title='Archived Role', status=Job.Status.OPEN, is_archived=True, openings=5)
        response = self._get()
        self.assertEqual(response.context['open_positions'], 0)

    def test_excludes_general_application_even_if_open(self):
        Job.objects.create(title=GENERAL_APPLICATION, status=Job.Status.OPEN, openings=99)
        response = self._get()
        self.assertEqual(response.context['open_positions'], 0)


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

    def test_initial_hold_is_excluded_from_the_funnel_entirely(self):
        """Future Prospects (a hold taken before ever being screened) is left
        out of every dashboard number now, not folded into Rejected - it has
        its own count shown separately (ctx['future_prospects_count']) and
        its own page instead. See dashboard.views.HRDashboardView's `base`
        queryset, which excludes dashboard.views.INITIAL_HOLD outright."""
        Candidate.objects.create(
            full_name='Held Early', email='held-early@example.com', job=self.job,
            status=Candidate.Status.SCREENING_HOLD, hold_from_status=Candidate.Status.OPEN)
        response = self._get()
        cv_screening = response.context['funnel'][0]
        label, count, flow, cat = cv_screening['drops'][0]
        self.assertEqual((label, flow, cat), ('Rejected', 'screened_out', 'red'))
        self.assertEqual(count, 0)  # excluded from the funnel, not folded in as a rejection
        self.assertEqual(response.context['future_prospects_count'], 1)

    def test_unable_to_connect_is_folded_into_yet_to_call(self):
        response = self._get()
        tele_screening = response.context['funnel'][1]
        drop_labels = [d[0] for d in tele_screening['drops']]
        self.assertNotIn('Unable to Connect', drop_labels)

    def test_yellow_pending_segments_sort_last_regardless_of_count(self):
        """"Yet to Call" (yellow - hasn't been actioned yet) always renders
        last in the bar, even when it outnumbers the other breakdown reasons
        - those are actual decisions/drops, this one is just still pending."""
        # 3 candidates still awaiting their tele-screening call (Yet to Call) -
        # deliberately more than the 1 rejected-after-call below.
        for i in range(3):
            c = Candidate.objects.create(full_name=f'Pending{i}', email=f'pending{i}@example.com', job=self.job)
            services.change_status(c, Candidate.Status.SHORTLISTED)
        rejected = Candidate.objects.create(full_name='Rejected', email='rejected@example.com', job=self.job)
        services.change_status(rejected, Candidate.Status.SHORTLISTED)
        services.change_status(rejected, Candidate.Status.REJECTED)

        tele_screening = self._get().context['funnel'][1]
        flows = [seg['flow'] for seg in tele_screening['segments']]
        self.assertLess(flows.index('rejected_after_call'), flows.index('call_pending'))


class DailyViewScreenedColumnTests(TestCase):
    """A screening-stage hold counts as a Daily View "Screened" action, same
    as an outright rejection (see dashboard.daily_view._rejected_or_held_at_screening_qs)."""

    def setUp(self):
        self.job = Job.objects.create(title='Analyst')
        self.today = timezone.localdate()

    def _held_candidate(self, name):
        c = Candidate.objects.create(full_name=name, email=f'{name}@example.com', job=self.job)
        services.record_creation(c)
        services.change_status(c, Candidate.Status.SCREENING_HOLD)
        return c

    def test_screening_hold_counts_in_the_screened_total(self):
        self._held_candidate('Held Early')
        [screened] = [c for c in daily_view.compute((self.today, self.today), None) if c['key'] == 'screened']
        self.assertEqual(screened['value'], 1)

    def test_screening_hold_is_labelled_rejected_in_the_breakdown(self):
        self._held_candidate('Held Early')
        [screened] = [c for c in daily_view.compute((self.today, self.today), None) if c['key'] == 'screened']
        breakdown = {b['label']: b['value'] for b in screened['breakdown']}
        self.assertEqual(breakdown.get('Rejected at Screening'), 1)
        self.assertNotIn('Hold', breakdown)

    def test_events_drilldown_includes_the_held_candidate(self):
        c = self._held_candidate('Held Early')
        rows = daily_view.events('screened', (self.today, self.today), None)
        matches = [r for r in rows if r['candidate'].pk == c.pk]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]['action'], 'Rejected at Screening')


class DailyViewCallsColumnTests(TestCase):
    """"Calls" is every Tele Screening action except "Yet to Call" itself -
    Shortlisted/Rejected/Hold at that stage, Unable to Connect/Call Back,
    and Attended - Decision Pending (only when nothing resolved it within
    the same range - see _merge_attended_into_resolution) - not just the 2
    call outcomes it used to track (which missed the rest entirely and
    undercounted the day's real call activity). See
    dashboard.daily_view._sources_for's 'calls' branch."""

    def setUp(self):
        self.job = Job.objects.create(title='Analyst')
        self.today = timezone.localdate()

    def _shortlisted_candidate(self, name):
        c = Candidate.objects.create(full_name=name, email=f'{name}@example.com', job=self.job)
        services.record_creation(c)
        services.change_status(c, Candidate.Status.SHORTLISTED)
        return c

    def _calls_total(self):
        [calls] = [c for c in daily_view.compute((self.today, self.today), None) if c['key'] == 'calls']
        return calls

    def test_shortlisted_after_call_counts(self):
        c = self._shortlisted_candidate('Rose')
        services.change_status(c, Candidate.Status.ROUND1)
        self.assertEqual(self._calls_total()['value'], 1)

    def test_rejected_after_call_counts(self):
        c = self._shortlisted_candidate('Rose')
        services.change_status(c, Candidate.Status.REJECTED)
        calls = self._calls_total()
        self.assertEqual(calls['value'], 1)
        breakdown = {b['label']: b['value'] for b in calls['breakdown']}
        self.assertEqual(breakdown.get('Rejected after Call'), 1)

    def test_hold_before_round1_counts(self):
        c = self._shortlisted_candidate('Rose')
        services.change_status(c, Candidate.Status.SCREENING_HOLD)
        calls = self._calls_total()
        self.assertEqual(calls['value'], 1)
        breakdown = {b['label']: b['value'] for b in calls['breakdown']}
        self.assertEqual(breakdown.get('Hold before Round 1'), 1)

    def test_unable_and_callback_outcomes_still_count(self):
        c = self._shortlisted_candidate('Rose')
        CommunicationLog.objects.create(
            candidate=c, channel=CommunicationLog.Channel.PHONE, outcome=CommunicationLog.Outcome.UNABLE)
        d = self._shortlisted_candidate('Nikhil')
        CommunicationLog.objects.create(
            candidate=d, channel=CommunicationLog.Channel.PHONE, outcome=CommunicationLog.Outcome.CALLBACK)
        self.assertEqual(self._calls_total()['value'], 2)

    def test_attended_decision_pending_counts_when_still_unresolved(self):
        """A call logged as Attended counts on its own only if nothing has
        resolved it within the same range - they're still genuinely
        pending as of the range's end."""
        c = self._shortlisted_candidate('Rose')
        CommunicationLog.objects.create(
            candidate=c, channel=CommunicationLog.Channel.PHONE, outcome=CommunicationLog.Outcome.ATTENDED)
        calls = self._calls_total()
        self.assertEqual(calls['value'], 1)
        breakdown = {b['label']: b['value'] for b in calls['breakdown']}
        self.assertEqual(breakdown.get('Attended - Decision Pending'), 1)

    def test_attended_then_shortlisted_in_the_same_range_counts_once(self):
        """Attended followed by its actual resolution (Shortlisted here),
        both within the same range, is one story with one outcome - not 2
        separate actions. The Attended entry is dropped in favour of the
        resolution it led to."""
        c = self._shortlisted_candidate('Rose')
        CommunicationLog.objects.create(
            candidate=c, channel=CommunicationLog.Channel.PHONE, outcome=CommunicationLog.Outcome.ATTENDED)
        services.change_status(c, Candidate.Status.ROUND1)
        calls = self._calls_total()
        self.assertEqual(calls['value'], 1)
        breakdown = {b['label']: b['value'] for b in calls['breakdown']}
        self.assertEqual(breakdown.get('Attended - Decision Pending'), 0)
        self.assertEqual(breakdown.get('Shortlisted After Call'), 1)

    def test_attended_then_rejected_in_the_same_range_counts_once(self):
        c = self._shortlisted_candidate('Rose')
        CommunicationLog.objects.create(
            candidate=c, channel=CommunicationLog.Channel.PHONE, outcome=CommunicationLog.Outcome.ATTENDED)
        services.change_status(c, Candidate.Status.REJECTED)
        calls = self._calls_total()
        self.assertEqual(calls['value'], 1)
        breakdown = {b['label']: b['value'] for b in calls['breakdown']}
        self.assertEqual(breakdown.get('Attended - Decision Pending'), 0)
        self.assertEqual(breakdown.get('Rejected after Call'), 1)

    def test_attended_then_held_in_the_same_range_counts_once(self):
        c = self._shortlisted_candidate('Rose')
        CommunicationLog.objects.create(
            candidate=c, channel=CommunicationLog.Channel.PHONE, outcome=CommunicationLog.Outcome.ATTENDED)
        services.change_status(c, Candidate.Status.SCREENING_HOLD)
        calls = self._calls_total()
        self.assertEqual(calls['value'], 1)
        breakdown = {b['label']: b['value'] for b in calls['breakdown']}
        self.assertEqual(breakdown.get('Attended - Decision Pending'), 0)
        self.assertEqual(breakdown.get('Hold before Round 1'), 1)

    def test_attended_resolved_outside_the_range_still_counts_as_pending(self):
        """Attended logged yesterday, resolved only today (outside a range
        scoped to yesterday alone) - within that range they were genuinely
        still pending, so the Attended entry stays."""
        from datetime import timedelta
        c = self._shortlisted_candidate('Rose')
        yesterday = self.today - timedelta(days=1)
        log = CommunicationLog.objects.create(
            candidate=c, channel=CommunicationLog.Channel.PHONE, outcome=CommunicationLog.Outcome.ATTENDED)
        CommunicationLog.objects.filter(pk=log.pk).update(logged_at=timezone.now() - timedelta(days=1))

        [calls] = [x for x in daily_view.compute((yesterday, yesterday), None) if x['key'] == 'calls']
        self.assertEqual(calls['value'], 1)
        breakdown = {b['label']: b['value'] for b in calls['breakdown']}
        self.assertEqual(breakdown.get('Attended - Decision Pending'), 1)

        # Resolving it today - outside yesterday's range - doesn't
        # retroactively change what already happened within it.
        services.change_status(c, Candidate.Status.ROUND1)
        [calls_again] = [x for x in daily_view.compute((yesterday, yesterday), None) if x['key'] == 'calls']
        self.assertEqual(calls_again['value'], 1)
        breakdown_again = {b['label']: b['value'] for b in calls_again['breakdown']}
        self.assertEqual(breakdown_again.get('Attended - Decision Pending'), 1)

    def test_yet_to_call_is_not_counted(self):
        """A candidate still waiting to be called (no action taken yet)
        isn't an action HR took, so contributes nothing to the total."""
        self._shortlisted_candidate('Rose')
        self.assertEqual(self._calls_total()['value'], 0)


class DailyViewCandidateGroupingTests(TestCase):
    """Several actions on the same candidate within a range (scheduled, then
    rescheduled, then rejected) used to render as 3 anonymous rows mixed in
    with everyone else's - confusing when trying to tell "how many actions"
    apart from "how many candidates". See dashboard.daily_view.compute's
    candidate_count and grouped_events()."""

    def setUp(self):
        self.job = Job.objects.create(title='Analyst')
        self.today = timezone.localdate()
        self.candidate = Candidate.objects.create(full_name='Rose', email='rose@example.com', job=self.job)
        services.record_creation(self.candidate)
        services.change_status(self.candidate, Candidate.Status.SHORTLISTED)
        services.change_status(self.candidate, Candidate.Status.ROUND1)
        self.interview = Interview.objects.create(
            candidate=self.candidate, round_type=Interview.RoundType.ROUND1,
            scheduled_date=timezone.now() + timezone.timedelta(days=1))
        InterviewReschedule.objects.create(
            interview=self.interview, previous_date=self.interview.scheduled_date,
            new_date=self.interview.scheduled_date + timezone.timedelta(hours=1))
        services.change_status(self.candidate, Candidate.Status.REJECTED)

    def test_candidate_count_is_lower_than_the_action_count_for_a_repeat_candidate(self):
        [round1] = [c for c in daily_view.compute((self.today, self.today), None) if c['key'] == 'round1']
        self.assertEqual(round1['value'], 3)  # scheduled, rescheduled, rejected
        self.assertEqual(round1['candidate_count'], 1)  # all the same candidate

    def test_grouped_events_keeps_one_candidates_actions_together_in_order(self):
        [group] = daily_view.grouped_events('round1', (self.today, self.today), None)
        self.assertEqual(group['candidate'], self.candidate)
        actions = [a['action'] for a in group['actions']]
        self.assertEqual(actions, [
            'Round 1 Interview Scheduled', 'Round 1 Interview Rescheduled', 'Rejected after Round 1',
        ])

    def test_grouped_events_keeps_unrelated_candidates_separate(self):
        other = Candidate.objects.create(full_name='Nikhil', email='nikhil@example.com', job=self.job)
        services.record_creation(other)
        services.change_status(other, Candidate.Status.SHORTLISTED)
        services.change_status(other, Candidate.Status.ROUND1)
        Interview.objects.create(
            candidate=other, round_type=Interview.RoundType.ROUND1,
            scheduled_date=timezone.now() + timezone.timedelta(days=1))

        groups = daily_view.grouped_events('round1', (self.today, self.today), None)
        self.assertEqual(len(groups), 2)
        self.assertEqual({g['candidate'].pk for g in groups}, {self.candidate.pk, other.pk})

    def test_drilldown_page_groups_by_candidate(self):
        user = get_user_model().objects.create_superuser('hr9', 'hr9@example.com', 'pw')
        self.client.force_login(user)
        response = self.client.get(reverse('daily_action_drilldown', args=['round1']),
                                   {'daily_from': self.today.isoformat(), 'daily_to': self.today.isoformat()})
        self.assertEqual(len(response.context['groups']), 1)
        self.assertEqual(response.context['total_actions'], 3)
        self.assertContains(response, '3 actions across 1 candidate')


class UserManagementTests(TestCase):
    """The in-app Manage Users page - add/edit accounts and their role,
    restricted to Admin (HR_ADMIN)."""

    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_user('admin', 'admin@turnb.com', 'pw')
        self.admin.groups.add(Group.objects.get_or_create(name=HR_ADMIN)[0])
        self.client.force_login(self.admin)

    def _create(self, **overrides):
        data = {
            'username': 'new.hire', 'first_name': 'New', 'last_name': 'Hire',
            'email': 'new.hire@turnb.com', 'is_active': 'on', 'role': RECRUITER,
            'password1': 'a-strong-passw0rd', 'password2': 'a-strong-passw0rd',
        }
        data.update(overrides)
        return self.client.post(reverse('user_add'), data)

    def test_recruiter_can_also_reach_the_users_page(self):
        """Recruiter has the same create/modify access as Admin throughout
        the main HR app, Users management included."""
        recruiter = get_user_model().objects.create_user('rec', 'rec@turnb.com', 'pw')
        recruiter.groups.add(Group.objects.get_or_create(name=RECRUITER)[0])
        self.client.force_login(recruiter)
        response = self.client.get(reverse('user_list'))
        self.assertEqual(response.status_code, 200)

    def test_other_roles_cannot_reach_the_users_page(self):
        viewer = get_user_model().objects.create_user('view', 'view@turnb.com', 'pw')
        viewer.groups.add(Group.objects.get_or_create(name=INTERVIEWER)[0])
        self.client.force_login(viewer)
        response = self.client.get(reverse('user_list'))
        self.assertEqual(response.status_code, 403)

    def test_admin_can_view_the_list(self):
        response = self.client.get(reverse('user_list'))
        self.assertEqual(response.status_code, 200)

    def test_creating_a_user_assigns_the_chosen_role_and_password(self):
        self._create()
        new_user = get_user_model().objects.get(username='new.hire')
        self.assertTrue(new_user.check_password('a-strong-passw0rd'))
        self.assertEqual([g.name for g in new_user.groups.all()], [RECRUITER])

    def test_password_can_be_skipped_on_creation(self):
        self._create(password1='', password2='')
        new_user = get_user_model().objects.get(username='new.hire')
        self.assertFalse(new_user.has_usable_password())

    def test_mismatched_passwords_are_rejected(self):
        response = self._create(password1='a-strong-passw0rd', password2='does-not-match')
        self.assertEqual(response.status_code, 200)  # redisplayed with the error
        self.assertFalse(get_user_model().objects.filter(username='new.hire').exists())

    def test_editing_a_user_changes_their_role(self):
        self._create()
        target = get_user_model().objects.get(username='new.hire')
        self.client.post(reverse('user_edit', args=[target.pk]), {
            'username': 'new.hire', 'first_name': 'New', 'last_name': 'Hire',
            'email': 'new.hire@turnb.com', 'is_active': 'on', 'role': INTERVIEWER,
            'password1': '', 'password2': '',
        })
        target.refresh_from_db()
        self.assertEqual([g.name for g in target.groups.all()], [INTERVIEWER])

    def _edit_admin(self, pk, **overrides):
        data = {
            'username': 'admin', 'first_name': '', 'last_name': '',
            'email': 'admin@turnb.com', 'is_active': 'on', 'role': HR_ADMIN,
            'password1': '', 'password2': '',
        }
        data.update(overrides)
        return self.client.post(reverse('user_edit', args=[pk]), data)

    def test_sole_admin_cannot_deactivate_their_own_account(self):
        response = self._edit_admin(self.admin.pk, is_active='')
        self.assertContains(response, 'only active Admin account')
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)

    def test_sole_admin_cannot_remove_their_own_admin_role(self):
        response = self._edit_admin(self.admin.pk, role=RECRUITER)
        self.assertContains(response, 'only active Admin account')
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.groups.filter(name=HR_ADMIN).exists())

    def test_toggle_active_flips_another_users_status(self):
        self._create()
        target = get_user_model().objects.get(username='new.hire')
        self.client.post(reverse('user_toggle_active', args=[target.pk]))
        target.refresh_from_db()
        self.assertFalse(target.is_active)

    def test_cannot_toggle_off_the_sole_admin(self):
        self.client.post(reverse('user_toggle_active', args=[self.admin.pk]))
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)

    def _second_admin(self):
        other = get_user_model().objects.create_user('admin2', 'admin2@turnb.com', 'pw')
        other.groups.add(Group.objects.get_or_create(name=HR_ADMIN)[0])
        return other

    def test_admin_can_step_down_once_another_admin_exists(self):
        """The rule is 'at least one Admin remains', not 'never touch your
        own account' - once a second Admin exists, self-demotion is fine.
        Recruiter can reach the Users page too, so this redirect target
        loads fine post-demotion - unlike the deactivate case below, there's
        no session-login side effect here worth skipping the fetch for."""
        self._second_admin()
        response = self._edit_admin(self.admin.pk, role=RECRUITER)
        self.assertRedirects(response, reverse('user_list'))
        self.admin.refresh_from_db()
        self.assertFalse(self.admin.groups.filter(name=HR_ADMIN).exists())

    def test_admin_can_deactivate_their_own_account_once_another_admin_exists(self):
        # fetch_redirect_response=False: deactivating your own account signs
        # this session out too (ModelBackend.get_user() rejects an inactive
        # user on the very next request), so following the redirect would
        # itself redirect again, to the login page.
        self._second_admin()
        response = self._edit_admin(self.admin.pk, is_active='')
        self.assertRedirects(response, reverse('user_list'), fetch_redirect_response=False)
        self.admin.refresh_from_db()
        self.assertFalse(self.admin.is_active)

    def test_cannot_demote_someone_else_who_is_the_last_admin(self):
        """The rule applies to editing anyone, not just yourself. Note this
        can't be shown by one Admin demoting another while both are Admin -
        excluding the target still leaves the actor, so the count never hits
        zero that way (correctly - that case just isn't dangerous). A
        superuser bypasses the Admin-group check to reach this page at all
        (GroupRequiredMixin), so it's the case that actually demonstrates the
        target-based (not self-based) rule: editing self.admin, the sole
        HR_ADMIN-group user, while acting as someone who isn't that group."""
        superuser = get_user_model().objects.create_superuser('root', 'root@turnb.com', 'pw')
        self.client.force_login(superuser)
        response = self._edit_admin(self.admin.pk, role=RECRUITER)
        self.assertContains(response, 'only active Admin account')
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.groups.filter(name=HR_ADMIN).exists())

    def test_toggle_off_works_once_another_admin_exists(self):
        self._second_admin()
        self.client.post(reverse('user_toggle_active', args=[self.admin.pk]))
        self.admin.refresh_from_db()
        self.assertFalse(self.admin.is_active)

    def test_a_non_admin_can_toggle_off_their_own_account(self):
        """The only rule is 'don't remove the last Admin' - a non-Admin
        deactivating themselves (from their own logged-in session) was never
        the concern the old unconditional self-check was guarding, so it's
        simply allowed. Matches the real case this was reported from: a
        superuser account assigned the Interviewer group (so it reaches this
        Admin-only page via the superuser bypass, same as an Admin would) had
        no Deactivate button at all on its own row."""
        panel = get_user_model().objects.create_superuser('panel', 'panel@turnb.com', 'pw')
        panel.groups.add(Group.objects.get_or_create(name=INTERVIEWER)[0])
        self.client.force_login(panel)
        self.client.post(reverse('user_toggle_active', args=[panel.pk]))
        panel.refresh_from_db()
        self.assertFalse(panel.is_active)

    def test_deactivate_button_is_shown_for_your_own_row(self):
        """Regression: user_list.html used to hide the toggle button
        entirely for your own row (a leftover from the old unconditional
        self-lock), even though the view-level rule is now count-based, not
        self-based - the button should show for every row."""
        response = self.client.get(reverse('user_list'))
        self.assertContains(
            response, reverse('user_toggle_active', args=[self.admin.pk]))


@override_settings(LOGIC_APP_EMAIL_SENDER_URL='https://logic.example/send-email')
class PasswordSelfServiceTests(TestCase):
    """Change Password (logged in) and Forgot Password (from either login
    page) - see HR_management/auth_views.py and HR_management/password_forms.py.
    The reset email goes through the same Logic App every other outbound
    email in this app uses, not Django's own mail backend."""

    def setUp(self):
        self.user = get_user_model().objects.create_user('hr7', 'hr7@example.com', 'OldPass123!')
        self.user.groups.add(Group.objects.get_or_create(name=HR_ADMIN)[0])
        self.client.force_login(self.user)

    def _ok_response(self):
        return mock.Mock(status_code=200, text='')

    def test_change_password_updates_it_and_lets_the_user_sign_in_with_it(self):
        response = self.client.post(reverse('password_change'), {
            'old_password': 'OldPass123!', 'new_password1': 'NewPass456!', 'new_password2': 'NewPass456!',
        })
        self.assertRedirects(response, reverse('hr_dashboard'))
        self.client.logout()
        self.assertTrue(self.client.login(username='hr7', password='NewPass456!'))

    def test_change_password_rejects_a_wrong_old_password(self):
        response = self.client.post(reverse('password_change'), {
            'old_password': 'WrongPass!', 'new_password1': 'NewPass456!', 'new_password2': 'NewPass456!',
        })
        self.assertEqual(response.status_code, 200)  # redisplayed with the error
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('OldPass123!'))

    def test_forgot_password_end_to_end(self):
        self.client.logout()
        with mock.patch('candidates.logic_app_mail.requests.post', return_value=self._ok_response()) as post:
            response = self.client.post(reverse('password_reset'), {'email': 'hr7@example.com'})
        self.assertRedirects(response, reverse('password_reset_done'))
        body = post.call_args.kwargs['json']['body']
        self.assertEqual(post.call_args.kwargs['json']['to'], 'hr7@example.com')

        match = re.search(r'/password/reset/confirm/([^/]+)/([^/\s]+)/', body)
        self.assertIsNotNone(match, f'No reset link found in email body: {body!r}')
        uidb64, token = match.group(1), match.group(2)

        # Following the emailed link (GET) swaps the token for a one-time
        # session token, same as clicking it in a real inbox would.
        confirm_url = reverse('password_reset_confirm', kwargs={'uidb64': uidb64, 'token': token})
        response = self.client.get(confirm_url, follow=True)
        self.assertEqual(response.status_code, 200)
        set_password_url = response.redirect_chain[-1][0]

        response = self.client.post(set_password_url, {
            'new_password1': 'BrandNew789!', 'new_password2': 'BrandNew789!',
        })
        self.assertRedirects(response, reverse('password_reset_complete'))
        self.assertTrue(self.client.login(username='hr7', password='BrandNew789!'))

    def test_forgot_password_does_not_reveal_whether_the_email_exists(self):
        self.client.logout()
        response = self.client.post(reverse('password_reset'), {'email': 'nobody@example.com'})
        self.assertRedirects(response, reverse('password_reset_done'))


class ReportsViewTests(TestCase):
    """Every number on Reports uses candidates.flows' own definitions
    ('ever_shortlisted', 'shortlisted_after_call', 'r1_cleared', 'hired') so
    it can never disagree with the Dashboard Overview funnel - and, like the
    Dashboard Summary page, leaves out General Application and Future
    Prospects entirely."""

    def setUp(self):
        self.user = get_user_model().objects.create_superuser('hr6', 'hr6@example.com', 'pw')
        self.client.force_login(self.user)
        self.job = Job.objects.create(title='Program Manager')
        general = Job.objects.create(title='General Application')

        def make(status_path, **kwargs):
            c = Candidate.objects.create(job=self.job, **kwargs)
            services.record_creation(c)
            for status in status_path:
                services.change_status(c, status)
            return c

        S = Candidate.Status
        self.c_open = make([], full_name='Open', email='open@example.com')
        self.c_shortlisted = make([S.SHORTLISTED], full_name='Shortlisted', email='shortlisted@example.com')
        self.c_round1 = make([S.SHORTLISTED, S.ROUND1], full_name='Round1', email='round1@example.com')
        self.c_cleared_r1 = make([S.SHORTLISTED, S.ROUND1, S.INTERVIEW],
                                 full_name='ClearedR1', email='clearedr1@example.com')
        self.c_hired = make([S.SHORTLISTED, S.ROUND1, S.INTERVIEW, S.FINAL_SELECTION, S.HIRED],
                            full_name='Hired', email='hired@example.com')

        self.c_general = Candidate.objects.create(
            job=general, full_name='General', email='general@example.com')
        services.record_creation(self.c_general)

        self.c_future_prospect = Candidate.objects.create(
            job=self.job, full_name='FutureProspect', email='fp@example.com')
        services.record_creation(self.c_future_prospect)
        services.change_status(self.c_future_prospect, S.SCREENING_HOLD)

    def _get(self, **params):
        return self.client.get(reverse('hr_reports'), params)

    def test_applicants_excludes_general_application_and_future_prospects(self):
        response = self._get()
        self.assertEqual(response.context['applicants'], 4)

    def test_applicants_is_qualified_plus_rejected_not_everyone(self):
        """Applicants = Qualified + Rejected-at-screening - Open/Unattended
        (c_open here, never screened) is deliberately left out, same as the
        funnel bar's own CV Screening peak."""
        response = self._get()
        self.assertEqual(response.context['qualified'], 4)
        self.assertEqual(response.context['applicants'], 4)  # not 5 - c_open excluded

    def test_qualified_ratio(self):
        response = self._get()
        self.assertEqual(response.context['qualified'], 4)
        self.assertEqual(response.context['qualified_pct'], 100.0)  # 4 of 4 applicants

    def test_r1_cleared_pct_is_based_on_those_who_reached_round1(self):
        response = self._get()
        # 3 reached Round 1 (Round1/ClearedR1/Hired); 2 of those cleared it.
        self.assertEqual(response.context['shortlisted'], 3)
        self.assertEqual(response.context['r1_cleared_pct'], round(2 / 3 * 100, 4))

    def test_r2_cleared(self):
        response = self._get()
        # Only ClearedR1 and Hired reached Round 1; only Hired went on to
        # reach Final Selection (clearing Round 2).
        self.assertEqual(response.context['r2_cleared'], 1)

    def test_hiring_ratio(self):
        response = self._get()
        self.assertEqual(response.context['hired'], 1)
        self.assertEqual(response.context['hired_pct_total'], 25.0)  # 1 of 4 applicants

    def test_by_job_never_lists_general_application(self):
        response = self._get()
        names = [row['name'] for row in response.context['by_job']]
        self.assertNotIn('General Application', names)
        self.assertIn('Program Manager', names)

    def test_by_job_has_exactly_one_row_per_role_not_one_per_candidate(self):
        """Regression: Candidate's default ordering (Meta.ordering =
        ['-created_at']) used to leak into the underlying SELECT DISTINCT
        (Django folds order_by() fields into distinct() unless order_by() is
        explicitly cleared first), so a role with 5 candidates rendered as 5
        identical-looking duplicate rows instead of 1."""
        response = self._get()
        rows = [row for row in response.context['by_job'] if row['name'] == 'Program Manager']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['applicants'], 4)  # 5 candidates, but c_open isn't an Applicant

    def test_r1_cleared_pct_is_none_not_zero_when_nobody_reached_round1(self):
        """None (not 0.0%) so the template can show '-' rather than a
        misleading 0.0% for a group nobody has even reached Round 1 in yet."""
        empty_job = Job.objects.create(title='Brand New Role')
        response = self._get(job=empty_job.pk)
        self.assertEqual(response.context['applicants'], 0)
        self.assertIsNone(response.context['r1_cleared_pct'])

    def test_date_range_filters_by_when_screened_not_when_applied(self):
        """The date filter goes by when the CV Screening decision happened
        (CandidateStatusHistory's SHORTLISTED/Rejected changed_at), not
        Candidate.created_at (when they applied) - see
        dashboard.views._with_screening_date."""
        from datetime import timedelta
        from django.utils import timezone as tz
        # c_shortlisted applied "today" (setUp's default), but was actually
        # screened a month ago.
        CandidateStatusHistory.objects.filter(
            candidate=self.c_shortlisted, new_status=Candidate.Status.SHORTLISTED
        ).update(changed_at=tz.now() - timedelta(days=30))
        today = tz.localdate()
        response = self._get(date_from=today.isoformat(), date_to=today.isoformat())
        # Excluded by screening date even though created_at is today -
        # leaving Round1/ClearedR1/Hired (all screened today).
        self.assertEqual(response.context['qualified'], 3)

    def test_date_range_includes_a_rejected_candidate_by_their_rejection_date(self):
        """A candidate rejected outright at screening (never Qualified) is
        dated by their Rejected event instead, not created_at."""
        from datetime import timedelta
        from django.utils import timezone as tz
        rejected = Candidate.objects.create(job=self.job, full_name='Rejected', email='rej@example.com')
        services.record_creation(rejected)
        services.change_status(rejected, Candidate.Status.REJECTED)
        CandidateStatusHistory.objects.filter(
            candidate=rejected, new_status=Candidate.Status.REJECTED
        ).update(changed_at=tz.now() - timedelta(days=30))

        today = tz.localdate()
        response = self._get(date_from=today.isoformat(), date_to=today.isoformat())
        self.assertEqual(response.context['applicants'], 4)  # rejected's screening date is out of range

        response = self._get(date_from=(tz.localdate() - timedelta(days=31)).isoformat(),
                             date_to=(tz.localdate() - timedelta(days=29)).isoformat())
        self.assertEqual(response.context['applicants'], 1)  # only the backdated rejection is in range

    def test_by_job_row_carries_both_previous_stage_and_total_pcts(self):
        """Each row needs both sets - the default view (% vs the stage right
        before it) and the expandable one (% vs Applicants overall) - see
        reports.html/_reports_row.html."""
        response = self._get()
        row = next(r for r in response.context['by_job'] if r['name'] == 'Program Manager')
        self.assertEqual(row['shortlisted_pct'], round(3 / 4 * 100, 4))        # vs Qualified
        self.assertEqual(row['shortlisted_pct_total'], round(3 / 4 * 100, 4))  # vs Applicants (same here)
        self.assertEqual(row['r2_cleared_pct'], round(1 / 2 * 100, 4))         # vs Round 1 Cleared
        self.assertEqual(row['r2_cleared_pct_total'], round(1 / 4 * 100, 4))   # vs Applicants

    def test_reports_page_renders_the_new_columns_and_expand_toggle(self):
        response = self._get()
        content = response.content.decode()
        self.assertIn('Round 1 Cleared', content)
        self.assertIn('Round 2 Cleared', content)
        self.assertIn('rep-toggle', content)
        self.assertIn('% of Applicants', content)

    def test_applicants_kpi_is_labelled_screened_applicants(self):
        response = self._get()
        self.assertContains(response, 'Screened Applicants')

    def test_by_job_row_carries_its_own_job_codes(self):
        """Program Manager (setUp's self.job) is a single Job/job code, so
        its Role row's job_codes breakdown is exactly that one code, with
        the same totals as the row itself."""
        response = self._get()
        row = next(r for r in response.context['by_job'] if r['name'] == 'Program Manager')
        self.assertEqual(len(row['job_codes']), 1)
        jc = row['job_codes'][0]
        self.assertEqual(jc['job_code'], self.job.job_code)
        self.assertEqual(jc['applicants'], row['applicants'])
        self.assertEqual(jc['hired'], row['hired'])

    def test_two_job_codes_under_the_same_title_are_both_listed(self):
        other_job = Job.objects.create(title='Program Manager')
        Candidate.objects.create(job=other_job, full_name='Other', email='other-pm@example.com',
                                 status=Candidate.Status.SHORTLISTED)
        response = self._get()
        rows = [r for r in response.context['by_job'] if r['name'] == 'Program Manager']
        self.assertEqual(len(rows), 1)  # still one Role row...
        codes = sorted(jc['job_code'] for jc in rows[0]['job_codes'])
        self.assertEqual(codes, sorted([self.job.job_code, other_job.job_code]))  # ...covering both codes

    def test_unassigned_role_has_no_job_codes_to_expand(self):
        Candidate.objects.create(full_name='No Job', email='nojob@example.com',
                                 status=Candidate.Status.SHORTLISTED)
        response = self._get()
        row = next(r for r in response.context['by_job'] if r['name'] == 'Unassigned')
        self.assertEqual(row['job_codes'], [])


class RolesTabTests(TestCase):
    """The Reports page's Roles tab - vacancy/job-code counts (not candidate
    counts, see jobs.models.Job) plus the two month-level trend charts."""

    def setUp(self):
        self.user = get_user_model().objects.create_superuser('hr7', 'hr7@example.com', 'pw')
        self.client.force_login(self.user)

    def _get(self, **params):
        return self.client.get(reverse('hr_reports'), params)

    def test_kpi_counts(self):
        Job.objects.create(title='Open Role A', status=Job.Status.OPEN)
        Job.objects.create(title='Open Role B', status=Job.Status.OPEN)
        closed_no_hire = Job.objects.create(title='Closed No Hire', status=Job.Status.CLOSED)
        closed_with_hire = Job.objects.create(title='Closed With Hire', status=Job.Status.CLOSED)
        Candidate.objects.create(job=closed_with_hire, full_name='Hired One',
                                 email='hired1@example.com', status=Candidate.Status.HIRED)
        Job.objects.create(title='General Application')  # excluded, like everywhere else

        response = self._get(view='roles')
        self.assertEqual(response.context['roles_total_opened'], 4)  # excludes General Application
        self.assertEqual(response.context['roles_active'], 2)
        self.assertEqual(response.context['roles_closed_with_hiring'], 1)
        self.assertEqual(response.context['roles_closed_without_hiring'], 1)
        self.assertEqual(response.context['roles_total_hired'], 1)

    def test_a_role_still_open_but_already_hired_counts_as_total_hired_not_closed_with_hiring(self):
        """Total Roles Hired counts any role that's hired someone, open or
        closed - Roles Closed With Hiring only counts once it's also
        actually closed (e.g. more openings still to fill)."""
        still_open = Job.objects.create(title='Still Hiring', status=Job.Status.OPEN, openings=3)
        Candidate.objects.create(job=still_open, full_name='First Hire',
                                 email='firsthire@example.com', status=Candidate.Status.HIRED)
        response = self._get(view='roles')
        self.assertEqual(response.context['roles_total_hired'], 1)
        self.assertEqual(response.context['roles_closed_with_hiring'], 0)

    def test_archived_roles_still_count_toward_total_opened(self):
        Job.objects.create(title='Archived Role', is_archived=True)
        response = self._get(view='roles')
        self.assertEqual(response.context['roles_total_opened'], 1)
        self.assertEqual(response.context['roles_active'], 0)  # archived isn't Active

    def test_job_filter_scopes_the_roles_kpis_to_just_that_job(self):
        target = Job.objects.create(title='Target Role', status=Job.Status.OPEN)
        Job.objects.create(title='Other Role', status=Job.Status.OPEN)
        response = self._get(view='roles', job=target.pk)
        self.assertEqual(response.context['roles_total_opened'], 1)

    def test_date_filter_scopes_roles_by_their_own_opening_date(self):
        """The From/To range has no "screening" concept for a vacancy - it
        filters Jobs by their own opening_date instead."""
        from datetime import timedelta
        today = timezone.localdate()
        in_range = Job.objects.create(title='In Range', opening_date=today)
        Job.objects.create(title='Out of Range', opening_date=today - timedelta(days=60))
        response = self._get(view='roles', date_from=(today - timedelta(days=7)).isoformat(),
                             date_to=today.isoformat())
        self.assertEqual(response.context['roles_total_opened'], 1)
        self.assertEqual(response.context['roles_active'], 1)

    def test_charts_carry_12_months_of_labels_and_series(self):
        response = self._get(view='roles')
        self.assertEqual(len(response.context['roles_chart_labels']), 12)
        self.assertEqual(len(response.context['roles_opened_series']), 12)
        self.assertEqual(len(response.context['candidates_applied_series']), 12)

    def test_a_role_opened_this_month_shows_up_in_the_current_months_bucket(self):
        Job.objects.create(title='Fresh Role', status=Job.Status.OPEN)
        response = self._get(view='roles')
        self.assertEqual(response.context['roles_opened_series'][-1], 1)  # last bucket = this month

    def test_view_defaults_to_applications_and_switches_to_roles(self):
        self.assertEqual(self._get().context['view'], 'applications')
        self.assertEqual(self._get(view='roles').context['view'], 'roles')


class ReportsRatioPrecisionTests(TestCase):
    """A percentage rounded to a whole number would round anything under
    0.5% down to a useless "0%", losing the precision the %/ratio switch
    needs to show something meaningful (a true 0.1% as "1:1000", not
    "1:inf" or "0%") - see dashboard.views._report_metrics's pct()."""

    def setUp(self):
        self.user = get_user_model().objects.create_superuser('hr8', 'hr8@example.com', 'pw')
        self.client.force_login(self.user)
        self.job = Job.objects.create(title='Rare Role')
        qualified = Candidate.objects.create(job=self.job, full_name='Qualified', email='q@example.com')
        services.record_creation(qualified)
        services.change_status(qualified, Candidate.Status.SHORTLISTED)
        Candidate.objects.bulk_create([
            Candidate(job=self.job, status=Candidate.Status.REJECTED, candidate_code=f'TESTR{i:04d}',
                     full_name=f'Rejected{i}', email=f'rejected{i}@example.com')
            for i in range(999)
        ])

    def test_a_true_tenth_of_a_percent_is_not_rounded_away(self):
        response = self.client.get(reverse('hr_reports'), {'job': self.job.pk})
        self.assertEqual(response.context['applicants'], 1000)
        self.assertEqual(response.context['qualified'], 1)
        self.assertAlmostEqual(response.context['qualified_pct'], 0.1, places=3)

    def test_page_still_displays_it_rounded_to_a_whole_percent(self):
        response = self.client.get(reverse('hr_reports'), {'job': self.job.pk})
        self.assertContains(response, 'data-pct="0.1"')
        self.assertNotContains(response, '0.1%')  # displayed text is rounded, the data attribute isn't

    def test_page_carries_the_denominator_for_a_genuine_zero_percent(self):
        """0 hired out of 1000 Applicants has no "1 in how many" to show -
        the denominator is carried in data-denom so the ratio switch can
        render "0:1000" instead of a nonsensical "1:0"."""
        response = self.client.get(reverse('hr_reports'), {'job': self.job.pk})
        self.assertEqual(response.context['hired_pct_total'], 0)
        self.assertContains(response, 'data-denom="1000"')

    def test_previous_stage_zero_shows_a_dash_even_when_an_earlier_stage_is_not(self):
        """Qualified (1) is non-zero, but nobody reached Round 1 -
        Shortlisted (0) is the denominator that actually matters for
        r1_cleared_pct, so it's a dash, not a 0% or a ratio computed
        against some other, earlier stage."""
        response = self.client.get(reverse('hr_reports'), {'job': self.job.pk})
        self.assertEqual(response.context['qualified'], 1)
        self.assertEqual(response.context['shortlisted'], 0)
        self.assertIsNone(response.context['r1_cleared_pct'])
        # The Round 1 Cleared cell renders a bare dash, never wrapped as a
        # toggleable .rep-pctval - so the %/Ratio switch can't turn it into
        # "0%"/"0:0" on its own.
        self.assertContains(response, '<span class="rep-num">0</span> <span class="rep-pct">(&ndash;)</span>')
