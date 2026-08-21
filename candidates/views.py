from django.conf import settings
from django.contrib import messages
from django.db.models import Count, Exists, Max, OuterRef, Q, Subquery
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.generic import DetailView, ListView, UpdateView

from interviews.models import Interview, InterviewReschedule
from jobs.models import Job

from . import bulk, cv_parser, services
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
TAB_STATUS_MAP = {key: status for key, _, status in REPOSITORY_TABS}
STATUS_TAB_MAP = {status: key for key, _, status in REPOSITORY_TABS}


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
    'to_recall': 'To Re-call — Attempted, Not Reached',
    'call_decision_pending': 'Call — Decision Pending',
    'r1_decision_pending': 'Round 1 — Decision Pending',
    'r2_decision_pending': 'Round 2 — Decision Pending',
    'r1_yet': 'Round 1 — Yet to Schedule', 'r1_cleared': 'Round 1 Cleared',
    'r1_scheduled': 'Round 1 Scheduled', 'r1_no_show': 'Round 1 — Not Turned Up',
    'r2_yet': 'Round 2 — Yet to Schedule', 'r2_cleared': 'Round 2 Cleared',
    'r2_scheduled': 'Round 2 Scheduled', 'r2_no_show': 'Round 2 — Not Turned Up',
    'on_hold': 'On Hold', 'hired': 'Hired', 'rejected': 'Rejected', 'blacklisted': 'Blacklisted',
    'screening_hold': 'Hold',
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
        qs = (Candidate.objects.select_related('job')
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

        job_id = self.request.GET.get('job')
        if job_id:
            qs = qs.filter(job_id=job_id)

        if self.request.GET.get('scope') == 'open':
            qs = qs.filter(job__status=Job.Status.OPEN, job__is_archived=False)

        source = self.request.GET.get('source')
        if source:
            qs = qs.filter(source=source)

        min_experience = self.request.GET.get('min_experience')
        if min_experience:
            qs = qs.filter(total_experience_years__gte=min_experience)

        date_from = self.request.GET.get('date_from')
        if date_from:
            qs = qs.filter(created_at__date__gte=date_from)
        date_to = self.request.GET.get('date_to')
        if date_to:
            qs = qs.filter(created_at__date__lte=date_to)

        q = self.request.GET.get('q')
        if q:
            qs = qs.filter(Q(full_name__icontains=q) | Q(email__icontains=q))

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
        scope = self.request.GET.get('scope', '')
        job_list = Job.objects.all().order_by('title')
        if scope == 'open':
            job_list = job_list.filter(status=Job.Status.OPEN, is_archived=False)
        ctx['jobs'] = job_list
        ctx['scope'] = scope
        ctx['sources'] = (Candidate.objects.exclude(source__isnull=True).exclude(source='')
                          .values_list('source', flat=True).distinct().order_by('source'))
        # every current filter except the tab, so switching tabs keeps the vacancy/scope/search
        # (the tab-specific switches are dropped so they reset when you leave their tab)
        params = self.request.GET.copy()
        for key in ('tab', 'hide_reapply', 'hide_called'):
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
        # At most one interview can be awaiting a result; while there is one, no
        # further interview may be scheduled (see interviews.forms.InterviewForm).
        ctx['open_interview'] = Interview.open_for(candidate).first()
        ctx['note_form'] = CandidateNoteForm()
        ctx['comm_form'] = CommunicationLogForm()

        stage_dates = {}
        for h in ctx['history'].order_by('changed_at'):
            stage_dates.setdefault(h.new_status, h.changed_at)
        ctx['stage_dates'] = stage_dates

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
        # actions the user can move this candidate to (all statuses except the current one)
        ctx['status_actions'] = [(v, l) for v, l in Candidate.Status.choices if v != candidate.status]
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
        candidate.save(update_fields=['job', 'updated_at'])
        new = candidate.job.title if candidate.job else 'None'
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
