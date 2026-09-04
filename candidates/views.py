from django.conf import settings
from django.contrib import messages
from django.db.models import Count, Exists, F, Max, OuterRef, Q, Subquery
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.generic import DetailView, ListView, UpdateView

from interviews.models import Interview, InterviewReschedule
from jobs.models import Job

from . import bulk, cv_parser, match_scoring, scoring, services, summarize
from .forms import (
    BulkUploadForm,
    CandidateApplicationForm,
    CandidateNoteForm,
    CommunicationLogForm,
    EducationFormSet,
    ExperienceFormSet,
)
from .models import (
    CAREERS,
    BulkUploadBatch,
    BulkUploadItem,
    Candidate,
    CandidateStatusHistory,
    HOLD_STAGES,
    CommunicationLog,
    Note,
    hold_label,
)
from .permissions import ANY_STAFF, HIRING_MANAGER, HR_ADMIN, RECRUITER, GroupRequiredMixin


def _performed_by(request):
    """Free-text 'Done by' from the action form, defaulting to the logged-in user."""
    return (request.POST.get('performed_by', '').strip()
            or request.user.get_full_name() or request.user.get_username())

STATUS = Candidate.Status

# The Hiring block's 5 progressive stage cards on the candidate profile page.
# Each stage lists the decisions available while a candidate sits there - the
# url names are the existing per-stage CandidateStatusActionView endpoints
# (candidates/urls.py), reused as-is rather than adding a new action view.
STAGE_ORDER = [STATUS.OPEN, STATUS.SHORTLISTED, STATUS.ROUND1, STATUS.INTERVIEW, STATUS.FINAL_SELECTION]
TERMINAL_STATUSES = (STATUS.HIRED, STATUS.REJECTED, STATUS.BLACKLISTED)

# Which Interview.RoundType values belong to "Round 1" vs "Round 2" of the
# Hiring block - mirrors dashboard/daily_view.py's NON_R1_ROUNDS split.
ROUND1_TYPES = (Interview.RoundType.ROUND1,)
ROUND2_TYPES = (Interview.RoundType.TECHNICAL, Interview.RoundType.MANAGERIAL,
                Interview.RoundType.FINAL, Interview.RoundType.HR)

HIRING_STAGES = [
    {'key': 'open', 'status': STATUS.OPEN, 'label': 'Open (Resume Received)', 'short_label': 'Open',
     'comm_channel': None, 'interview_rounds': (), 'actions': [
         {'url': 'candidate_shortlist', 'target': STATUS.SHORTLISTED, 'label': 'Shortlist', 'tone': 'advance'},
         {'url': 'candidate_screening_hold', 'target': STATUS.SCREENING_HOLD, 'label': 'Hold', 'tone': 'hold'},
         {'url': 'candidate_reject', 'target': STATUS.REJECTED, 'label': 'Reject', 'tone': 'reject'},
         {'url': 'candidate_blacklist', 'target': STATUS.BLACKLISTED, 'label': 'Blacklist', 'tone': 'blacklist', 'require_reason': True},
     ]},
    {'key': 'shortlisted', 'status': STATUS.SHORTLISTED, 'label': 'Shortlisted', 'short_label': 'Shortlisted',
     'comm_channel': CommunicationLog.Channel.PHONE, 'interview_rounds': (), 'actions': [
         {'url': 'candidate_round1', 'target': STATUS.ROUND1, 'label': 'Move to Round 1', 'tone': 'advance'},
         {'url': 'candidate_screening_hold', 'target': STATUS.SCREENING_HOLD, 'label': 'Hold', 'tone': 'hold'},
         {'url': 'candidate_reject', 'target': STATUS.REJECTED, 'label': 'Reject', 'tone': 'reject'},
         {'url': 'candidate_blacklist', 'target': STATUS.BLACKLISTED, 'label': 'Blacklist', 'tone': 'blacklist', 'require_reason': True},
     ]},
    {'key': 'round1', 'status': STATUS.ROUND1, 'label': 'Round 1', 'short_label': 'Round 1',
     'comm_channel': CommunicationLog.Channel.INTERVIEW, 'interview_rounds': ROUND1_TYPES, 'actions': [
         {'url': 'candidate_interview_stage', 'target': STATUS.INTERVIEW, 'label': 'Move to Round 2', 'tone': 'advance'},
         {'url': 'candidate_screening_hold', 'target': STATUS.SCREENING_HOLD, 'label': 'Hold', 'tone': 'hold'},
         {'url': 'candidate_reject', 'target': STATUS.REJECTED, 'label': 'Reject', 'tone': 'reject'},
     ]},
    {'key': 'round2', 'status': STATUS.INTERVIEW, 'label': 'Round 2', 'short_label': 'Round 2',
     'comm_channel': CommunicationLog.Channel.INTERVIEW, 'interview_rounds': ROUND2_TYPES, 'actions': [
         {'url': 'candidate_final_selection', 'target': STATUS.FINAL_SELECTION, 'label': 'Move to Final Selection', 'tone': 'advance'},
         {'url': 'candidate_screening_hold', 'target': STATUS.SCREENING_HOLD, 'label': 'Hold', 'tone': 'hold'},
         {'url': 'candidate_reject', 'target': STATUS.REJECTED, 'label': 'Reject', 'tone': 'reject'},
     ]},
    {'key': 'final', 'status': STATUS.FINAL_SELECTION, 'label': 'Final Decision', 'short_label': 'Final Decision',
     'comm_channel': None, 'interview_rounds': (), 'actions': [
         {'url': 'candidate_hire', 'target': STATUS.HIRED, 'label': 'Hire', 'tone': 'advance'},
         {'url': 'candidate_screening_hold', 'target': STATUS.SCREENING_HOLD, 'label': 'Hold', 'tone': 'hold'},
         {'url': 'candidate_reject', 'target': STATUS.REJECTED, 'label': 'Reject', 'tone': 'reject'},
     ]},
]


def _build_hiring_stages(candidate, history):
    """Render data for the Hiring block's 5 stage cards: which stage is
    active, which are done (with their decision reconstructed from history),
    and which are still locked ahead. `history` must already be a list/qs
    fully evaluated (it's walked more than once)."""
    labels = dict(Candidate.Status.choices)
    history = sorted(history, key=lambda h: (h.changed_at, h.id))

    if candidate.status == STATUS.SCREENING_HOLD:
        lookup_status = candidate.hold_from_status or STATUS.OPEN
    else:
        lookup_status = candidate.status
    try:
        active_index = STAGE_ORDER.index(lookup_status)
    except ValueError:
        active_index = None  # terminal: Hired / Rejected / Blacklisted

    def entered_row(stage_status):
        return next((h for h in history if h.new_status == stage_status), None)

    reached = [i for i, s in enumerate(HIRING_STAGES) if entered_row(s['status']) is not None]
    max_reached = max(reached) if reached else -1

    def decision_after(stage_status, since_row):
        """The history row that moved the candidate off `stage_status` for
        good: the next real stage or any terminal status, whichever comes
        first after they entered it. A Hold taken at this stage is skipped
        over (its own row lands here as SCREENING_HOLD, not a target) so the
        eventual resume-and-advance/reject row is what's reported."""
        idx = STAGE_ORDER.index(stage_status)
        targets = set(TERMINAL_STATUSES)
        if idx + 1 < len(STAGE_ORDER):
            targets.add(STAGE_ORDER[idx + 1])
        since = since_row.changed_at if since_row else None
        for h in history:
            if since is not None and h.changed_at <= since:
                continue
            if h.new_status in targets:
                return h
        return None

    stages = []
    for i, stage in enumerate(HIRING_STAGES):
        entry = dict(stage)
        if active_index is not None:
            is_done, is_active = i < active_index, i == active_index
        else:
            # No current stage (terminal candidate) - everything up to the
            # furthest stage ever reached is "done"; anything past where they
            # exited was never reached at all, so it stays locked.
            is_done, is_active = i <= max_reached, False
        entry.update(is_done=is_done, is_active=is_active,
                     is_locked=not is_done and not is_active, decision=None)
        entered = entered_row(stage['status'])
        entry['entered'] = entered
        if is_done:
            row = decision_after(stage['status'], entered)
            if row:
                action = next((a for a in stage['actions'] if a['target'] == row.new_status), None)
                entry['decision'] = {
                    'label': action['label'] if action else labels.get(row.new_status, row.new_status),
                    'tone': action['tone'] if action else 'advance',
                    'remarks': row.remarks, 'who': row.performed_by or row.changed_by, 'when': row.changed_at,
                }
        stages.append(entry)
    return stages, active_index

REPOSITORY_TABS = [
    ('open', 'Open Applications', STATUS.OPEN),
    ('shortlisted', 'Shortlisted', STATUS.SHORTLISTED),
    ('round1', 'Round 1', STATUS.ROUND1),
    ('interview', 'Interview', STATUS.INTERVIEW),
    ('final_selection', 'Final Selection', STATUS.FINAL_SELECTION),
    ('hired', 'Hired', STATUS.HIRED),
    ('rejected', 'Rejected', STATUS.REJECTED),
    ('blacklisted', 'Blacklisted', STATUS.BLACKLISTED),
    ('screening_hold', 'Hold', STATUS.SCREENING_HOLD),
]
HOLD_TAB = 'screening_hold'
TAB_STATUS_MAP = {key: status for key, _, status in REPOSITORY_TABS}
STATUS_TAB_MAP = {status: key for key, _, status in REPOSITORY_TABS}

# Candidate Repository's Score filter - non-overlapping bands over match_score
# (unscored candidates have match_score=None, which every lookup here excludes,
# so "no score selected" and "not yet scored" never get confused).
SCORE_BANDS = [
    ('lt20', 'Below 20', {'match_score__lt': 20}),
    ('20-50', '20 – 50', {'match_score__gte': 20, 'match_score__lte': 50}),
    ('50-80', '50 – 80', {'match_score__gt': 50, 'match_score__lte': 80}),
    ('gt80', 'Above 80', {'match_score__gt': 80}),
]
SCORE_BAND_FILTERS = {key: lookup for key, _, lookup in SCORE_BANDS}


LIST_URL_SESSION_KEY = 'last_candidate_list_url'


def _remember_list_url(request):
    """Record the candidate list the user is looking at (tab, filters, search and
    all) so that opening a candidate and then acting on them returns to exactly
    that list rather than a bare, unfiltered repository page."""
    request.session[LIST_URL_SESSION_KEY] = request.get_full_path()


def _remembered_list_url(request):
    return request.session.get(LIST_URL_SESSION_KEY) or ''


class RemembersListUrlMixin:
    """Mix into a candidate list page so it records itself as "where I was
    working"; candidate pages opened from here come back to it."""

    def get(self, request, *args, **kwargs):
        _remember_list_url(request)
        return super().get(request, *args, **kwargs)


def _repository_back_url(candidate, request=None):
    """Where "Back" goes from a candidate's own pages: the list the user came
    from, else the Candidate Repository on the tab this candidate sits in."""
    if request is not None:
        remembered = _remembered_list_url(request)
        if remembered:
            return remembered
    tab = STATUS_TAB_MAP.get(candidate.status, 'open')
    return f"{reverse('candidate_repository')}?tab={tab}"

# Readable titles for the dashboard funnel drill-downs (?flow=…)
FLOW_TITLES = {
    'all': 'All Candidates', 'open': 'Screening Pending', 'unfit': 'Unfit Resumes',
    'ever_shortlisted': 'Screened & Shortlisted', 'call_pending': 'Yet to Call',
    'shortlisted_after_call': 'Shortlisted After Call', 'unable_to_connect': 'Unable to Connect',
    'call_decision_pending': 'Call — Decision Pending',
    'rejected_after_call': 'Rejected After Call',
    'r1_decision_pending': 'Round 1 — Decision Pending',
    'r2_decision_pending': 'Round 2 — Decision Pending',
    'r1_yet': 'Round 1 — Yet to Schedule', 'r1_cleared': 'Round 1 Cleared',
    'r1_scheduled': 'Round 1 Scheduled', 'r1_no_show': 'Round 1 — Not Turned Up',
    'rejected_after_round1': 'Rejected After Round 1',
    'r2_yet': 'Round 2 — Yet to Schedule', 'r2_cleared': 'Round 2 Cleared',
    'r2_scheduled': 'Round 2 Scheduled', 'r2_no_show': 'Round 2 — Not Turned Up',
    'rejected_after_round2': 'Rejected After Round 2',
    'on_hold': 'On Hold', 'hired': 'Hired', 'rejected': 'Rejected', 'blacklisted': 'Blacklisted',
    'rejected_after_final': 'Rejected After Final Round',
    'screening_hold': 'Hold', 'hold_before_shortlist': 'Hold (Before Shortlist)',
    'hold_before_round1': 'Hold (Before Round 1)', 'hold_before_round2': 'Hold (Before Round 2)',
    'hold_before_final': 'Hold (Before Final Decision)', 'hold_after_final': 'Hold (After Final Decision)',
}


class ApplicationCreateView(View):
    template_name = 'candidates/application_form.html'

    def get_job(self, job_code):
        return get_object_or_404(Job, job_code=job_code, status=Job.Status.OPEN, is_archived=False)

    def get(self, request, job_code):
        job = self.get_job(job_code)
        context = {
            'job': job,
            'form': CandidateApplicationForm(),
            'education_formset': EducationFormSet(prefix='edu'),
            'experience_formset': ExperienceFormSet(prefix='exp'),
        }
        return render(request, self.template_name, context)

    def post(self, request, job_code):
        job = self.get_job(job_code)
        form = CandidateApplicationForm(request.POST, request.FILES)
        education_formset = EducationFormSet(request.POST, prefix='edu')
        experience_formset = ExperienceFormSet(request.POST, prefix='exp')

        if form.is_valid() and education_formset.is_valid() and experience_formset.is_valid():
            candidate = form.save(commit=False)
            candidate.job = job
            # On the careers site the applicant picks the vacancy, so the position
            # they applied for is simply its title.
            candidate.role_applied = job.title
            # CAREERS (not 'Careers Portal') so this matches the Logic App intake proc
            candidate.source = candidate.source or CAREERS
            services.submit_application(candidate)

            education_formset.instance = candidate
            education_formset.save()
            experience_formset.instance = candidate
            experience_formset.save()

            return redirect('application_thank_you', candidate_code=candidate.candidate_code)

        return render(request, self.template_name, {
            'job': job,
            'form': form,
            'education_formset': education_formset,
            'experience_formset': experience_formset,
        })


class ApplicationThankYouView(DetailView):
    model = Candidate
    slug_field = 'candidate_code'
    slug_url_kwarg = 'candidate_code'
    context_object_name = 'candidate'
    template_name = 'candidates/thank_you.html'


# ---------------------------------------------------------------------------
# HR admin: Candidate Repository
# ---------------------------------------------------------------------------


class CandidateRepositoryListView(RemembersListUrlMixin, GroupRequiredMixin, ListView):
    model = Candidate
    template_name = 'candidates/repository.html'
    context_object_name = 'candidates'
    allowed_groups = ANY_STAFF

    def get_tab(self):
        return self.request.GET.get('tab', 'open')

    def _apply_flow(self, qs, flow):
        from .flows import flow_filter
        return flow_filter(qs, flow)

    def _filtered_queryset(self):
        """Candidates matching every filter that applies across tabs (vacancy,
        open-vacancies-only, source, applied date range, search) but not the
        tab/status itself. Shared by get_queryset() and the tab-pill counts,
        so switching a filter updates both the list and the counts together."""
        qs = Candidate.objects.all()

        job_id = self.request.GET.get('job')
        if job_id:
            qs = qs.filter(job_id=job_id)

        if self.request.GET.get('scope') == 'open':
            qs = qs.filter(job__status=Job.Status.OPEN, job__is_archived=False)

        source = self.request.GET.get('source')
        if source:
            qs = qs.filter(source=source)

        score_band = SCORE_BAND_FILTERS.get(self.request.GET.get('score'))
        if score_band:
            qs = qs.filter(**score_band)

        date_from = self.request.GET.get('date_from')
        if date_from:
            qs = qs.filter(created_at__date__gte=date_from)
        date_to = self.request.GET.get('date_to')
        if date_to:
            qs = qs.filter(created_at__date__lte=date_to)

        q = self.request.GET.get('q')
        if q:
            qs = qs.filter(Q(full_name__icontains=q) | Q(email__icontains=q))

        return qs

    def get_queryset(self):
        last_action = (CandidateStatusHistory.objects
                       .filter(candidate=OuterRef('pk')).order_by('-changed_at', '-id')
                       .values('changed_at')[:1])
        next_interview = (Interview.objects
                          .filter(candidate=OuterRef('pk')).order_by('-scheduled_date')
                          .values('scheduled_date')[:1])
        # 'reapply' = the same email exists on another candidate record
        dup = Candidate.objects.filter(email=OuterRef('email')).exclude(pk=OuterRef('pk'))
        # 'called' = a phone call was already logged with an inconclusive outcome
        # (Unable to connect / Call back), so the candidate isn't a fresh, un-called one
        called = CommunicationLog.objects.filter(
            candidate=OuterRef('pk'), channel=CommunicationLog.Channel.PHONE,
            outcome__in=[CommunicationLog.Outcome.UNABLE, CommunicationLog.Outcome.CALLBACK])
        qs = (self._filtered_queryset().select_related('job')
              .annotate(last_action_at=Subquery(last_action),
                        interview_at=Subquery(next_interview),
                        reapply=Exists(dup),
                        called=Exists(called)))

        # A "flow" link (from the dashboard Overview) filters by a derived stage
        # set and overrides the normal current-status tab filter.
        flow = self.request.GET.get('flow')
        if flow:
            qs = self._apply_flow(qs, flow)
        else:
            status = TAB_STATUS_MAP.get(self.get_tab())
            if status is not None:
                qs = qs.filter(status=status)

        # Status filter. On the Hold tab every row is the same stored status, so
        # what distinguishes them - and what the dropdown offers - is the stage
        # the hold was taken at ("Round 1 Hold").
        status_filter = self.request.GET.get('status')
        if status_filter:
            if self.get_tab() == HOLD_TAB and not flow:
                qs = qs.filter(hold_from_status=status_filter)
            else:
                qs = qs.filter(status=status_filter)

        # Tab-specific switches: hide reapplicants (Open list) / hide already-called (Shortlisted)
        if self.request.GET.get('hide_reapply'):
            qs = qs.filter(reapply=False)
        if self.request.GET.get('hide_called'):
            qs = qs.filter(called=False)

        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        # A flow view isn't a single status, so don't highlight/act on a tab.
        ctx['tab'] = 'all' if self.request.GET.get('flow') else self.get_tab()
        ctx['flow'] = self.request.GET.get('flow', '')
        ctx['flow_title'] = FLOW_TITLES.get(ctx['flow'])
        ctx['tabs'] = REPOSITORY_TABS
        # Counts for the tab pills - per status, under the vacancy/source/date/search
        # filters currently applied (but not the tab or its own status/hide switches,
        # so every pill stays comparable to the others).
        ctx['tab_counts'] = {row['status']: row['n'] for row in
                            self._filtered_queryset().values('status').annotate(n=Count('id'))}
        ctx['total_candidates'] = sum(ctx['tab_counts'].values())
        ctx['awaiting_count'] = ctx['tab_counts'].get(STATUS.OPEN, 0)
        scope = self.request.GET.get('scope', '')
        job_list = Job.objects.all().order_by('title')
        if scope == 'open':
            job_list = job_list.filter(status=Job.Status.OPEN, is_archived=False)
        ctx['jobs'] = job_list
        ctx['scope'] = scope
        ctx['sources'] = (Candidate.objects.exclude(source__isnull=True).exclude(source='')
                          .values_list('source', flat=True).distinct().order_by('source'))
        ctx['score_bands'] = SCORE_BANDS
        ctx['score'] = self.request.GET.get('score', '')
        # The Hold tab lists one stored status, so it offers the named holds
        # instead; every other tab offers the pipeline statuses.
        if ctx['tab'] == HOLD_TAB:
            ctx['status_options'] = [(s, hold_label(s)) for s in HOLD_STAGES]
        else:
            ctx['status_options'] = list(Candidate.Status.choices)
        # every current filter except the tab, so switching tabs keeps the vacancy/scope/search
        # (the tab-specific switches are dropped so they reset when you leave their tab;
        # so is the status, which means a different thing on the Hold tab)
        params = self.request.GET.copy()
        for key in ('tab', 'hide_reapply', 'hide_called', 'status'):
            params.pop(key, None)
        ctx['preserved_qs'] = params.urlencode()
        u = self.request.user
        ctx['is_hr_admin'] = u.is_superuser or u.groups.filter(name=HR_ADMIN).exists()
        return ctx


class AllCandidatesListView(RemembersListUrlMixin, GroupRequiredMixin, ListView):
    """Flat, full list of every candidate — Name, Email, Job, Status, View —
    reached from the 'Candidate Repository' heading and exportable to Excel."""
    model = Candidate
    template_name = 'candidates/all_candidates.html'
    context_object_name = 'candidates'
    allowed_groups = ANY_STAFF

    def get_queryset(self):
        return Candidate.objects.select_related('job').order_by('full_name')


GENERAL_APPLICATION = 'General Application'
# Filter value for candidates whose applied position was never captured.
NO_ROLE = '__none__'


class GeneralApplicationsListView(RemembersListUrlMixin, GroupRequiredMixin, ListView):
    """Everyone filed under the "General Application" vacancy - i.e. the intake
    couldn't match the position they asked for to an open vacancy - shown with the
    position they actually applied for, and filterable by it."""
    model = Candidate
    template_name = 'candidates/general_applications.html'
    context_object_name = 'candidates'
    allowed_groups = ANY_STAFF

    def base_queryset(self):
        return (Candidate.objects.select_related('job')
                .filter(job__title__iexact=GENERAL_APPLICATION))

    def selected_roles(self):
        return [r for r in self.request.GET.getlist('role') if r]

    def get_queryset(self):
        qs = self.base_queryset()

        roles = self.selected_roles()
        if roles:
            condition = Q(role_applied__in=[r for r in roles if r != NO_ROLE])
            if NO_ROLE in roles:
                condition |= Q(role_applied__isnull=True) | Q(role_applied='')
            qs = qs.filter(condition)

        q = self.request.GET.get('q')
        if q:
            qs = qs.filter(Q(full_name__icontains=q) | Q(email__icontains=q)
                           | Q(role_applied__icontains=q))
        return qs.order_by('-created_at')

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        base = self.base_queryset()
        ctx['role_options'] = (base.exclude(role_applied__isnull=True).exclude(role_applied='')
                               .values('role_applied').annotate(n=Count('id'))
                               .order_by('role_applied'))
        ctx['no_role_count'] = base.filter(Q(role_applied__isnull=True) | Q(role_applied='')).count()
        ctx['no_role_value'] = NO_ROLE
        ctx['selected_roles'] = self.selected_roles()
        ctx['q'] = self.request.GET.get('q', '')
        ctx['total'] = base.count()
        u = self.request.user
        ctx['is_hr_admin'] = u.is_superuser or u.groups.filter(name=HR_ADMIN).exists()
        return ctx


class CandidateTimelineView(GroupRequiredMixin, DetailView):
    model = Candidate
    template_name = 'candidates/timeline.html'
    context_object_name = 'candidate'
    allowed_groups = ANY_STAFF

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        candidate = self.object
        ctx['back_url'] = _repository_back_url(candidate, self.request)
        ctx['back_label'] = 'Back to Candidates'
        ctx['history'] = candidate.history.select_related('changed_by').all()
        ctx['notes'] = candidate.notes.select_related('author').all()
        ctx['communication_logs'] = candidate.communication_logs.select_related('logged_by').all()
        ctx['attachments'] = candidate.attachments.all()
        ctx['offers'] = candidate.offers.all()
        ctx['interviews'] = candidate.interviews.select_related('interviewer').all()
        ctx['note_form'] = CandidateNoteForm()

        # Unified activity feed: every action (status changes, notes, calls,
        # interviews, offers) in one chronological list with remarks.
        labels = dict(Candidate.Status.choices)
        # A hold is named after the stage it was taken at ("Interview Hold"), so
        # walk history oldest-first to note which stage each hold came from -
        # a row that *leaves* a hold has to name it the same way it was entered.
        held_from, hold_source = '', {}
        for h in sorted(ctx['history'], key=lambda h: (h.changed_at, h.id)):
            hold_source[h.id] = held_from
            if h.new_status == STATUS.SCREENING_HOLD:
                held_from = h.old_status

        def status_label(status, came_from=''):
            if status == STATUS.SCREENING_HOLD:
                return hold_label(came_from)
            return labels.get(status, status)

        events = []
        for h in ctx['history']:
            was = status_label(h.old_status, hold_source[h.id]) if h.old_status else '—'
            events.append({
                'when': h.changed_at, 'icon': 'arrow-right-circle',
                'title': f"Status: {was} → {status_label(h.new_status, h.old_status)}",
                'detail': h.remarks, 'who': h.performed_by or h.changed_by})
        for n in ctx['notes']:
            events.append({'when': n.created_at, 'icon': 'sticky',
                           'title': 'Note added', 'detail': n.text, 'who': n.author})
        for l in ctx['communication_logs']:
            title = l.get_channel_display() + (f": {l.subject}" if l.subject else " logged")
            events.append({'when': l.logged_at, 'icon': 'telephone',
                           'title': title, 'detail': l.message, 'who': l.logged_by})
        for i in ctx['interviews']:
            events.append({
                'when': i.scheduled_date, 'icon': 'calendar-event',
                'title': f"{i.get_round_type_display()} interview — {i.get_status_display()} / {i.get_result_display()}",
                'detail': i.feedback, 'who': i.interviewer or i.created_by})
        # Rescheduling rewrites the interview row, so the trail of what moved
        # lives in these rows rather than in the interview itself.
        reschedules = (InterviewReschedule.objects.filter(interview__candidate=candidate)
                       .select_related('interview', 'previous_interviewer',
                                       'new_interviewer', 'changed_by'))
        for r in reschedules:
            events.append({
                'when': r.changed_at, 'icon': 'calendar-event',
                'title': f"{r.interview.get_round_type_display()} interview rescheduled",
                'detail': r.summary, 'who': r.changed_by})
        for o in ctx['offers']:
            events.append({'when': o.sent_at or o.created_at, 'icon': 'file-earmark-text',
                           'title': f"Offer {o.get_status_display()}", 'detail': None, 'who': o.created_by})
        events.sort(key=lambda e: e['when'], reverse=True)
        ctx['activity'] = events

        # Most recent status change, shown above the Update Status form so HR can
        # see the current state and who set it (history is ordered -changed_at)
        last = ctx['history'].first()
        ctx['last_status'] = last
        ctx['last_status_label'] = status_label(last.new_status, last.old_status) if last else ''

        u = self.request.user
        ctx['is_hr_admin'] = u.is_superuser or u.groups.filter(name=HR_ADMIN).exists()
        ctx['can_revert'] = ctx['is_hr_admin'] or u.groups.filter(name=RECRUITER).exists()
        ctx['all_jobs'] = Job.objects.all().order_by('title')
        # same email seen on another record => reapply
        ctx['reapply'] = Candidate.objects.filter(email=candidate.email).exclude(pk=candidate.pk).exists()

        ctx['is_on_hold'] = candidate.status == STATUS.SCREENING_HOLD
        hiring_stages, active_stage_index = _build_hiring_stages(candidate, ctx['history'])
        # Scoped per stage (not candidate-wide): a still-open Round 1
        # interview must not read as a "Reschedule" once the candidate has
        # already moved on to Round 2 - each stage only looks at interviews
        # of its own round_type(s).
        for stage in hiring_stages:
            rounds = stage['interview_rounds']
            stage['open_interview'] = next(
                (i for i in ctx['interviews'] if i.round_type in rounds
                 and i.status in Interview.OPEN_STATUSES and i.result == Interview.Result.PENDING),
                None) if rounds else None
        ctx['hiring_stages'] = hiring_stages
        ctx['active_stage_index'] = active_stage_index

        if active_stage_index is None:
            outcome_label = {STATUS.HIRED: 'Hired', STATUS.REJECTED: 'Rejected',
                             STATUS.BLACKLISTED: 'Blacklisted'}.get(candidate.status, candidate.status_label)
            outcome_color = {STATUS.HIRED: '#0e6f6b', STATUS.REJECTED: '#dc3545',
                             STATUS.BLACKLISTED: '#212529'}.get(candidate.status, '#6c757d')
            ctx['final_status'] = {'label': outcome_label, 'color': outcome_color}
        else:
            ctx['final_status'] = {'label': f'In Progress · {HIRING_STAGES[active_stage_index]["label"]}',
                                   'color': '#17a2b8'}

        # Stage Dates (SLA) tracker: Applied, plus every later stage actually
        # reached so far (stages the candidate hasn't gotten to yet don't
        # have a date and are left off rather than shown blank). "Open" is
        # skipped as its own node - it's set at intake, the same moment as
        # "Applied", so showing both is a redundant, same-day duplicate. A
        # candidate who exited to a terminal outcome (Hired/Rejected/
        # Blacklisted) gets that outcome appended too, so e.g. someone
        # rejected at screening shows "Applied -> Rejected" instead of
        # stopping at "Open" as if that were where they ended up.
        sla_stages = [{'label': 'Applied', 'date': candidate.created_at}]
        for stage in hiring_stages:
            if stage['status'] == STATUS.OPEN:
                continue
            if stage['entered']:
                sla_stages.append({'label': stage['short_label'], 'date': stage['entered'].changed_at})
        if active_stage_index is None and last and last.new_status == candidate.status:
            sla_stages.append({'label': ctx['final_status']['label'], 'date': last.changed_at,
                               'color': ctx['final_status']['color']})
        ctx['sla_stages'] = sla_stages

        comm_logs_by_channel = {}
        for log in ctx['communication_logs']:
            comm_logs_by_channel.setdefault(log.channel, []).append(log)
        ctx['comm_logs_by_channel'] = comm_logs_by_channel

        return ctx


class CandidateUpdateView(GroupRequiredMixin, UpdateView):
    model = Candidate
    form_class = CandidateApplicationForm
    template_name = 'candidates/candidate_form.html'
    allowed_groups = (HR_ADMIN, RECRUITER)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['back_url'] = _repository_back_url(self.object, self.request)
        ctx['back_label'] = 'Back to Candidates'
        return ctx

    def form_valid(self, form):
        messages.success(self.request, f'{form.instance.full_name} updated.')
        return super().form_valid(form)

    def get_success_url(self):
        return reverse('candidate_timeline', args=[self.object.pk])


class CandidateChangeJobView(GroupRequiredMixin, View):
    """Manually re-map a candidate to a different vacancy (e.g. move a
    'General Application' to a specific opening)."""
    allowed_groups = (HR_ADMIN, RECRUITER, HIRING_MANAGER)

    def post(self, request, pk):
        candidate = get_object_or_404(Candidate, pk=pk)
        old = candidate.job.title if candidate.job else 'None'
        job_id = request.POST.get('job') or None
        candidate.job = get_object_or_404(Job, pk=job_id) if job_id else None
        new = candidate.job.title if candidate.job else 'None'
        update_fields = ['job', 'updated_at']
        if old != new:
            # A score reflects the JD it was matched against - moving vacancies
            # makes it stale, so clear it rather than leave a misleading number.
            candidate.match_score = None
            candidate.match_breakdown = None
            candidate.match_rationale = None
            candidate.match_error = None
            candidate.match_scored_at = None
            candidate.match_state = Candidate.MatchState.PENDING
            update_fields += ['match_score', 'match_breakdown', 'match_rationale',
                              'match_error', 'match_scored_at', 'match_state']
        candidate.save(update_fields=update_fields)
        if old != new:
            Note.objects.create(candidate=candidate, author=request.user,
                                text=f'Vacancy changed: {old} -> {new}')
            messages.success(request, f'Vacancy updated to "{new}".')
        return redirect('candidate_timeline', pk=pk)


class CandidateChangeSourceView(GroupRequiredMixin, View):
    """Manually edit a candidate's recruitment source."""
    allowed_groups = (HR_ADMIN, RECRUITER, HIRING_MANAGER)

    def post(self, request, pk):
        candidate = get_object_or_404(Candidate, pk=pk)
        old = candidate.source or 'None'
        new = request.POST.get('source', '').strip() or None
        candidate.source = new
        candidate.save(update_fields=['source', 'updated_at'])
        if old != (new or 'None'):
            Note.objects.create(candidate=candidate, author=request.user,
                                text=f'Source changed: {old} -> {new or "None"}')
        messages.success(request, 'Source updated.')
        return redirect('candidate_timeline', pk=pk)


class CandidateSetStatusView(GroupRequiredMixin, View):
    """Move a candidate to a chosen status straight from their page (the
    'Update status' box), logging it to the activity history."""
    allowed_groups = (HR_ADMIN, RECRUITER, HIRING_MANAGER)

    def post(self, request, pk):
        candidate = get_object_or_404(Candidate, pk=pk)
        target = request.POST.get('status')
        if target not in {s for s, _ in Candidate.Status.choices}:
            messages.error(request, 'Please choose a valid action.')
            return redirect('candidate_timeline', pk=pk)
        reason = request.POST.get('reason', '').strip()
        performed_by = _performed_by(request)
        if target == STATUS.BLACKLISTED:
            services.blacklist_candidate(candidate, reason, user=request.user, performed_by=performed_by)
        else:
            services.change_status(candidate, target, user=request.user,
                                   remarks=reason or None, performed_by=performed_by)
        messages.success(request, f'{candidate.full_name} moved to "{candidate.status_label}".')
        return redirect('candidate_timeline', pk=pk)


class CandidateRescoreView(GroupRequiredMixin, View):
    """Recreate this one candidate's match score - after they're edited, the
    mapped role's JD changes, or the scoring prompt/model changes."""
    allowed_groups = (HR_ADMIN, RECRUITER)

    def post(self, request, pk):
        candidate = get_object_or_404(Candidate, pk=pk)
        MS = Candidate.MatchState
        if not candidate.job or candidate.job.title.strip().lower() == GENERAL_APPLICATION.lower():
            messages.error(request, "This candidate isn't mapped to a role, so they can't be scored.")
        elif not match_scoring.is_configured():
            messages.error(request, 'Scoring is not configured - set AZURE_OPENAI_ENDPOINT '
                                    'and AZURE_OPENAI_KEY.')
        elif candidate.match_state == MS.SCORING:
            messages.info(request, 'Already scoring this candidate.')
        else:
            # Reopen it for scoring even if it was already DONE - score_one()
            # only claims rows in PENDING_STATES (PENDING/ERROR).
            Candidate.objects.filter(pk=pk).update(match_state=MS.PENDING)
            scoring.start_one(candidate)
            messages.success(request, 'Re-scoring this candidate - refresh in a few seconds.')
        return redirect('candidate_timeline', pk=pk)


class CandidateScoreStatusView(GroupRequiredMixin, View):
    """Poll target for the profile page while a Re-score is running."""
    allowed_groups = ANY_STAFF

    def get(self, request, pk):
        candidate = get_object_or_404(Candidate, pk=pk)
        MS = Candidate.MatchState
        return JsonResponse({
            'state': candidate.match_state,
            'finished': candidate.match_state != MS.SCORING,
        })


class CandidateRegenerateSummaryView(GroupRequiredMixin, View):
    """Re-read this candidate's stored resume and refresh their AI CV
    summary - after they're edited, or after the summary prompt/model
    changes - without re-uploading the file."""
    allowed_groups = (HR_ADMIN, RECRUITER)

    def post(self, request, pk):
        candidate = get_object_or_404(Candidate, pk=pk)
        CSS = Candidate.CVSummaryState
        if not candidate.resume_blob_url:
            messages.error(request, "This candidate's resume was linked via URL, not "
                                    "uploaded, so there's no stored file to re-read.")
        elif not cv_parser.is_configured():
            messages.error(request, 'CV parsing is not configured - set LOGIC_APP_CV_PARSER_URL.')
        else:
            claimed = Candidate.objects.filter(pk=pk).exclude(cv_summary_state=CSS.RUNNING).update(
                cv_summary_state=CSS.RUNNING, cv_summary_error=None)
            if not claimed:
                messages.info(request, 'Already regenerating the AI CV summary for this candidate.')
            else:
                summarize.start(candidate)
                messages.success(request, 'Regenerating the AI CV summary - this can take a '
                                          "couple of minutes. Refresh in a bit if it doesn't update itself.")
        return redirect('candidate_timeline', pk=pk)


class CandidateSummaryStatusView(GroupRequiredMixin, View):
    """Poll target for the profile page while a summary regeneration is running."""
    allowed_groups = ANY_STAFF

    def get(self, request, pk):
        candidate = get_object_or_404(Candidate, pk=pk)
        CSS = Candidate.CVSummaryState
        return JsonResponse({
            'state': candidate.cv_summary_state,
            'finished': candidate.cv_summary_state != CSS.RUNNING,
        })


class AddNoteView(GroupRequiredMixin, View):
    allowed_groups = ANY_STAFF

    def post(self, request, pk):
        candidate = get_object_or_404(Candidate, pk=pk)
        form = CandidateNoteForm(request.POST)
        if form.is_valid():
            note = form.save(commit=False)
            note.candidate = candidate
            note.author = request.user
            note.save()
            messages.success(request, 'Note added.')
        return redirect('candidate_timeline', pk=pk)


class AddCommunicationLogView(GroupRequiredMixin, View):
    allowed_groups = ANY_STAFF

    def post(self, request, pk):
        candidate = get_object_or_404(Candidate, pk=pk)
        form = CommunicationLogForm(request.POST)
        if form.is_valid():
            log = form.save(commit=False)
            log.candidate = candidate
            log.logged_by = request.user
            log.save()
            messages.success(request, 'Communication logged.')
        return redirect('candidate_timeline', pk=pk)


class CandidateStatusActionView(GroupRequiredMixin, View):
    """Generic POST-only status transition for the Candidate Repository
    workflow (Open -> Shortlisted -> Round1 -> Interview -> FinalSelection
    -> Hired, with Reject/Blacklist available from any stage)."""
    target_status = None
    require_reason = False
    allowed_groups = (HR_ADMIN, RECRUITER, HIRING_MANAGER)

    def post(self, request, pk):
        candidate = get_object_or_404(Candidate, pk=pk)
        reason = request.POST.get('reason', '').strip()
        performed_by = _performed_by(request)
        if self.require_reason and not reason:
            messages.error(request, 'A reason is required.')
            return redirect(request.POST.get('next') or reverse('candidate_timeline', args=[pk]))

        if self.target_status == STATUS.BLACKLISTED:
            services.blacklist_candidate(candidate, reason, user=request.user, performed_by=performed_by)
        else:
            services.change_status(candidate, self.target_status, user=request.user,
                                   remarks=reason or None, performed_by=performed_by)
        messages.success(request, f'{candidate.full_name} moved to "{candidate.status_label}".')
        next_url = request.POST.get('next')
        return redirect(next_url or reverse('candidate_timeline', args=[pk]))


class CandidateRevertLastActionView(GroupRequiredMixin, View):
    """Undo the most recent status change: remove it from history entirely and
    roll the candidate back, so the dashboard funnel no longer counts it."""
    allowed_groups = (HR_ADMIN, RECRUITER)

    def post(self, request, pk):
        candidate = get_object_or_404(Candidate, pk=pk)
        last = candidate.history.order_by('-changed_at', '-id').first()
        next_url = request.POST.get('next') or reverse('candidate_timeline', args=[pk])
        # keep the very first 'Applied' entry (old_status is blank) — nothing to undo before it
        if not last or not last.old_status:
            messages.error(request, 'There is no action to undo.')
            return redirect(next_url)
        prev_status, undone = last.old_status, last.new_status
        last.delete()  # remove the accidental transition from history
        if undone == STATUS.BLACKLISTED:  # unwind blacklist side-effects
            candidate.is_blacklisted = False
            bl = candidate.blacklist_entries.order_by('-blacklisted_at').first()
            if bl:
                bl.delete()
        candidate.status = prev_status
        candidate.hold_from_status = services.hold_source_from_history(candidate)
        candidate.save(update_fields=['status', 'hold_from_status', 'is_blacklisted', 'updated_at'])
        messages.success(request, f'Last action undone — {candidate.full_name} is back to "{candidate.status_label}".')
        return redirect(next_url)


class CandidateDeleteView(GroupRequiredMixin, View):
    """Permanently delete a candidate and all their related records (HR Admin only)."""
    allowed_groups = (HR_ADMIN,)

    def post(self, request, pk):
        candidate = get_object_or_404(Candidate, pk=pk)
        name = candidate.full_name
        candidate.delete()
        messages.success(request, f'Candidate "{name}" and all their records were deleted.')
        next_url = request.POST.get('next')
        if next_url and f'/candidates/{pk}/' in next_url:
            # Deleted from the candidate's own page - that URL is gone now, so
            # fall back to the list the user was working in.
            next_url = None
        return redirect(next_url or _remembered_list_url(request) or reverse('candidate_repository'))


class CandidateBulkRejectView(GroupRequiredMixin, View):
    """Reject several candidates at once, ticked on a list page. Each one gets
    its own history entry, exactly as rejecting them one by one would."""
    allowed_groups = (HR_ADMIN, RECRUITER, HIRING_MANAGER)

    def post(self, request):
        ids = request.POST.getlist('ids')
        reason = request.POST.get('reason', '').strip()
        performed_by = _performed_by(request)
        candidates = Candidate.objects.filter(pk__in=ids).exclude(
            status__in=[STATUS.REJECTED, STATUS.BLACKLISTED])

        rejected = 0
        for candidate in candidates:
            services.change_status(candidate, STATUS.REJECTED, user=request.user,
                                   remarks=reason or None, performed_by=performed_by)
            rejected += 1

        skipped = len(ids) - rejected
        if rejected:
            message = f'{rejected} candidate{"s" if rejected != 1 else ""} moved to Rejected.'
            if skipped:
                message += f' {skipped} already rejected or blacklisted and left unchanged.'
            messages.success(request, message)
        else:
            messages.info(request, 'No candidates were rejected.')
        next_url = request.POST.get('next')
        return redirect(next_url or _remembered_list_url(request) or reverse('candidate_repository'))


class CandidateBulkStatusActionView(GroupRequiredMixin, View):
    """Move several ticked candidates to the same status at once (Shortlist,
    Hold, ...). Each one still gets its own history entry, exactly as acting
    on them one by one would; candidates already at that status or in a
    terminal one (Rejected/Blacklisted/Hired) are left untouched."""
    target_status = None
    allowed_groups = (HR_ADMIN, RECRUITER, HIRING_MANAGER)

    def post(self, request):
        ids = request.POST.getlist('ids')
        reason = request.POST.get('reason', '').strip()
        performed_by = _performed_by(request)
        candidates = Candidate.objects.filter(pk__in=ids).exclude(
            status__in=[STATUS.REJECTED, STATUS.BLACKLISTED, STATUS.HIRED, self.target_status])

        moved = 0
        for candidate in candidates:
            services.change_status(candidate, self.target_status, user=request.user,
                                   remarks=reason or None, performed_by=performed_by)
            moved += 1

        skipped = len(ids) - moved
        label = self.target_status.label
        if moved:
            message = f'{moved} candidate{"s" if moved != 1 else ""} moved to {label}.'
            if skipped:
                message += f' {skipped} left unchanged (already {label} or a terminal status).'
            messages.success(request, message)
        else:
            messages.info(request, 'No candidates were updated.')
        next_url = request.POST.get('next')
        return redirect(next_url or _remembered_list_url(request) or reverse('candidate_repository'))


class CandidateBulkBlacklistView(GroupRequiredMixin, View):
    """Blacklist several ticked candidates at once - a reason is required,
    same as blacklisting one from their own page."""
    allowed_groups = (HR_ADMIN, RECRUITER, HIRING_MANAGER)

    def post(self, request):
        ids = request.POST.getlist('ids')
        reason = request.POST.get('reason', '').strip()
        performed_by = _performed_by(request)
        if not reason:
            messages.error(request, 'A reason is required.')
            return redirect(request.POST.get('next') or reverse('candidate_repository'))

        candidates = Candidate.objects.filter(pk__in=ids).exclude(status=STATUS.BLACKLISTED)
        blacklisted = 0
        for candidate in candidates:
            services.blacklist_candidate(candidate, reason, user=request.user, performed_by=performed_by)
            blacklisted += 1

        if blacklisted:
            messages.success(
                request, f'{blacklisted} candidate{"s" if blacklisted != 1 else ""} blacklisted.')
        else:
            messages.info(request, 'No candidates were updated.')
        next_url = request.POST.get('next')
        return redirect(next_url or _remembered_list_url(request) or reverse('candidate_repository'))


class CandidateBulkDeleteView(GroupRequiredMixin, View):
    """Delete several candidates at once, ticked on the Candidate Repository
    (HR Admin only, same as deleting one)."""
    allowed_groups = (HR_ADMIN,)

    def post(self, request):
        ids = request.POST.getlist('ids')
        candidates = list(Candidate.objects.filter(pk__in=ids))
        deleted = len(candidates)
        for candidate in candidates:
            candidate.delete()
        if deleted:
            messages.success(
                request, f'{deleted} candidate{"s" if deleted != 1 else ""} '
                         f'and all their records were deleted.')
        else:
            messages.info(request, 'No candidates were selected.')
        next_url = request.POST.get('next')
        return redirect(next_url or _remembered_list_url(request) or reverse('candidate_repository'))


class BulkRejectClosedVacanciesView(GroupRequiredMixin, View):
    """Move every still-active candidate under a CLOSED vacancy to Rejected.
    Hired and already-terminal candidates are left untouched."""
    allowed_groups = (HR_ADMIN,)
    ACTIVE = (STATUS.OPEN, STATUS.SHORTLISTED, STATUS.ROUND1, STATUS.INTERVIEW, STATUS.FINAL_SELECTION)

    def post(self, request):
        performed_by = _performed_by(request)
        cands = list(Candidate.objects.filter(
            job__status=Job.Status.CLOSED, status__in=self.ACTIVE).select_related('job'))
        history = [CandidateStatusHistory(
            candidate=c, old_status=c.status, new_status=STATUS.REJECTED,
            changed_by=request.user, performed_by=performed_by,
            remarks=f'Auto-rejected: vacancy "{c.job.title}" is closed.') for c in cands]
        Candidate.objects.filter(pk__in=[c.pk for c in cands]).update(
            status=STATUS.REJECTED, updated_at=timezone.now())
        CandidateStatusHistory.objects.bulk_create(history, batch_size=100)
        messages.success(request, f'{len(cands)} candidate(s) in closed vacancies moved to Rejected.')
        return redirect('job_manage_list')


# ---------------------------------------------------------------------------
# HR admin: Bulk Upload CV
# ---------------------------------------------------------------------------


ALLOWED_CV_EXTENSIONS = ('.pdf', '.doc', '.docx')

RECENT_BATCH_COUNT = 8


def recent_batches(limit=RECENT_BATCH_COUNT):
    """Last few bulk uploads with their counts, so HR can reopen the results of
    a batch after navigating away from the progress screen."""
    waiting = (BulkUploadItem.Status.PENDING, BulkUploadItem.Status.PARSING)
    return (BulkUploadBatch.objects.select_related('job')
            .annotate(
                total=Count('items'),
                success=Count('items', filter=Q(items__status=BulkUploadItem.Status.SUCCESS)),
                errors=Count('items', filter=Q(items__status=BulkUploadItem.Status.ERROR)),
                waiting=Count('items', filter=Q(items__status__in=waiting)),
            )[:limit])


def _validate_cv_files(files):
    """Returns (errors, ok_files). Rejects the wrong file types / oversized files
    up front rather than paying for a Logic App run that is bound to fail."""
    max_files = int(getattr(settings, 'BULK_UPLOAD_MAX_FILES', 25))
    max_bytes = int(getattr(settings, 'BULK_UPLOAD_MAX_MB', 10)) * 1024 * 1024
    errors = []
    if len(files) > max_files:
        errors.append(f'Upload at most {max_files} CVs at a time (you selected {len(files)}).')
        return errors, []
    ok = []
    for f in files:
        if not f.name.lower().endswith(ALLOWED_CV_EXTENSIONS):
            errors.append(f'{f.name}: only PDF, DOC and DOCX files can be parsed.')
        elif f.size > max_bytes:
            errors.append(f'{f.name}: larger than {max_bytes // (1024 * 1024)} MB.')
        else:
            ok.append(f)
    return errors, ok


class BulkUploadCVView(GroupRequiredMixin, View):
    """Step 1: select vacancy + source, upload multiple CVs. Each file is queued
    as a BulkUploadItem and parsed in the background by the `cv-parse-single`
    Logic App (same extraction as the careers mailbox intake); candidates are
    created only for CVs that parse. HR watches progress on the results screen."""
    template_name = 'candidates/bulk_upload.html'
    allowed_groups = (HR_ADMIN, RECRUITER)

    def get(self, request):
        return render(request, self.template_name, {
            'form': BulkUploadForm(),
            'parser_configured': cv_parser.is_configured(),
            'recent_batches': recent_batches(),
        })

    def post(self, request):
        form = BulkUploadForm(request.POST)
        files = request.FILES.getlist('cvs')
        errors, files = _validate_cv_files(files)
        for error in errors:
            messages.error(request, error)
        if not files:
            messages.error(request, 'Select at least one CV file to upload.')
        elif form.is_valid():
            batch = BulkUploadBatch.objects.create(
                job=form.cleaned_data['job'],
                source=form.cleaned_data['source'],
                created_by=request.user,
                performed_by=_performed_by(request),
            )
            for f in files:
                BulkUploadItem.objects.create(batch=batch, filename=f.name[:255], cv_file=f)
            bulk.start_batch(batch)
            return redirect('candidate_bulk_progress', pk=batch.pk)
        return render(request, self.template_name, {
            'form': form,
            'parser_configured': cv_parser.is_configured(),
            'recent_batches': recent_batches(),
        })


class BulkUploadProgressView(GroupRequiredMixin, View):
    """Step 2: live progress while the CVs are parsed, then the results -
    which CVs became candidates and which failed, with a retry for the failures."""
    template_name = 'candidates/bulk_progress.html'
    allowed_groups = (HR_ADMIN, RECRUITER)

    def get(self, request, pk):
        batch = get_object_or_404(BulkUploadBatch.objects.select_related('job'), pk=pk)
        items, counts = bulk.summarise(batch)
        return render(request, self.template_name, {
            'batch': batch, 'items': items, 'counts': counts,
            'back_url': reverse('candidate_bulk_upload'),
            'back_label': 'Back to Bulk Upload',
        })


class BulkUploadStatusView(GroupRequiredMixin, View):
    """Poll target for the progress page: just the counts, so the page can show
    a live bar and reload itself once the batch is finished."""
    allowed_groups = (HR_ADMIN, RECRUITER)

    def get(self, request, pk):
        batch = get_object_or_404(BulkUploadBatch, pk=pk)
        _, counts = bulk.summarise(batch)
        return JsonResponse(counts)


class BulkUploadRetryView(GroupRequiredMixin, View):
    """Re-queue the failed CVs in a batch (same files, no re-upload needed)."""
    allowed_groups = (HR_ADMIN, RECRUITER)

    def post(self, request, pk):
        batch = get_object_or_404(BulkUploadBatch, pk=pk)
        requeued = batch.items.filter(status=BulkUploadItem.Status.ERROR).update(
            status=BulkUploadItem.Status.PENDING, error_message=None)
        if requeued:
            bulk.start_batch(batch)
            messages.success(request, f'Retrying {requeued} CV(s).')
        else:
            messages.info(request, 'Nothing to retry in this batch.')
        return redirect('candidate_bulk_progress', pk=batch.pk)


class ScoreCandidatesView(GroupRequiredMixin, View):
    """Step 1: pick a role. Shows every vacancy (never "General Application" -
    those candidates aren't mapped to a role to score against) with how many of
    its candidates are still pending a score."""
    template_name = 'candidates/score_candidates.html'
    allowed_groups = (HR_ADMIN, RECRUITER)

    def get(self, request):
        jobs = (Job.objects.exclude(title__iexact=GENERAL_APPLICATION)
                .annotate(
                    candidate_total=Count('candidates'),
                    candidate_pending=Count('candidates', filter=Q(
                        candidates__match_state__in=scoring.PENDING_STATES)),
                    candidate_scored=Count('candidates', filter=Q(
                        candidates__match_state=Candidate.MatchState.DONE)),
                )
                .filter(candidate_total__gt=0)
                .order_by('-created_on'))
        return render(request, self.template_name, {
            'jobs': jobs,
            'scoring_configured': match_scoring.is_configured(),
        })


class ScoreCandidatesRunView(GroupRequiredMixin, View):
    """Kick off (or resume) scoring every pending candidate on one vacancy."""
    allowed_groups = (HR_ADMIN, RECRUITER)

    def post(self, request, job_id):
        job = get_object_or_404(Job, pk=job_id)
        if job.title.strip().lower() == GENERAL_APPLICATION.lower():
            messages.error(request, 'General Application candidates aren\'t mapped to a '
                                    'role, so they can\'t be scored.')
            return redirect('candidate_score')
        if not match_scoring.is_configured():
            messages.error(request, 'Scoring is not configured - set AZURE_OPENAI_ENDPOINT '
                                    'and AZURE_OPENAI_KEY.')
            return redirect('candidate_score')
        scoring.start_job_scoring(job)
        return redirect('candidate_score_progress', job_id=job.pk)


class ScoreCandidatesProgressView(GroupRequiredMixin, View):
    """Step 2: live progress while candidates are scored, then the results -
    every candidate mapped to the role with their score."""
    template_name = 'candidates/score_progress.html'
    allowed_groups = (HR_ADMIN, RECRUITER)

    def get(self, request, job_id):
        job = get_object_or_404(Job, pk=job_id)
        counts = scoring.summarise(job.pk)
        candidates = (job.candidates.order_by(
            F('match_score').desc(nulls_last=True), '-match_scored_at'))
        return render(request, self.template_name, {
            'job': job, 'counts': counts, 'candidates': candidates,
            'back_url': reverse('candidate_score'),
            'back_label': 'Back to Score Candidates',
        })


class ScoreCandidatesStatusView(GroupRequiredMixin, View):
    """Poll target for the progress page: just the counts, so the page can
    show a live bar and reload itself once the run is finished."""
    allowed_groups = (HR_ADMIN, RECRUITER)

    def get(self, request, job_id):
        return JsonResponse(scoring.summarise(job_id))
