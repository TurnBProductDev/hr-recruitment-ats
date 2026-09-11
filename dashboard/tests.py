"""Dashboard summary tests.

Run against sqlite so the live Azure DB is never touched:
    DB_ENGINE=sqlite python manage.py test dashboard
"""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from candidates import services
from candidates.models import Candidate
from candidates.permissions import HR_ADMIN, INTERVIEWER, RECRUITER
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
