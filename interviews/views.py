import logging

from django.contrib import messages
from django.db.models import BooleanField, Case, Value, When
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html
from django.utils.http import url_has_allowed_host_and_scheme
from django.views import View
from django.views.generic import CreateView, ListView, UpdateView

from candidates import logic_app_mail, services
from candidates.models import Candidate, CommunicationLog, Note
from candidates.permissions import (
    ANY_STAFF, HIRING_MANAGER, HR_ADMIN, INTERVIEWER, RECRUITER, GroupRequiredMixin, in_interviewer_portal,
)

from notifications import services as notifications

from . import calendar_sync, graph_client, invites, slot_emails
from .forms import InterviewAllocationForm, InterviewForm, InterviewResultForm
from .models import (
    INTERVIEW_DURATION, Interview, InterviewReschedule, InterviewRequest,
    interviewer_conflict_message, open_interview_message, open_interview_request_message,
)

logger = logging.getLogger(__name__)


def _is_ajax(request):
    return request.headers.get('X-Requested-With') == 'XMLHttpRequest'


def _portal_url(request, candidate_id):
    """The interviewer portal's view of this candidate - linked from the
    calendar meeting, since interviewers don't use the HR profile page."""
    return request.build_absolute_uri(reverse('interviewer_candidate', args=[candidate_id]))


def _tell_interviewer_cancelled(interviewer, candidate, round_label, when=None, note=''):
    """In-app notification + email to the interviewer whose interview HR just
    cancelled. Best-effort: a failed email never blocks the cancel."""
    if interviewer is None:
        return
    when_text = f' on {timezone.localtime(when):%d %b %Y, %H:%M}' if when else ''
    notifications.notify(
        interviewer, title=f'Interview cancelled - {candidate.full_name}',
        message=f'HR cancelled the {round_label} interview with {candidate.full_name}{when_text}.'
                + (f' Note: {note}' if note else ''),
        url=reverse('interviewer_home'))
    try:
        slot_emails.notify_interviewer_cancelled(
            interviewer=interviewer, candidate=candidate, round_type=round_label, when=when, note=note)
    except logic_app_mail.EmailSendError as exc:
        logger.warning('Could not email the cancellation for %s to %s: %s',
                       candidate.pk, interviewer.get_username(), exc)


class InterviewSchedulerListView(GroupRequiredMixin, ListView):
    """Scheduled interviews still awaiting a result. Once a result is marked the
    interview is completed and drops off this list. Overdue-but-unmarked
    interviews stay listed (shown as 'Result Pending')."""
    model = Interview
    template_name = 'interviews/scheduler.html'
    context_object_name = 'interviews'
    allowed_groups = ANY_STAFF

    def get_queryset(self):
        qs = (Interview.objects.filter(
                status__in=Interview.OPEN_STATUSES, result=Interview.Result.PENDING)
              .select_related('candidate', 'candidate__job', 'interviewer')
              .annotate(is_upcoming=Case(
                  When(scheduled_date__gt=timezone.now(), then=Value(True)),
                  default=Value(False), output_field=BooleanField())))
        q = self.request.GET.get('q')
        if q:
            qs = qs.filter(candidate__full_name__icontains=q)
        return qs.order_by('scheduled_date')


class InterviewScheduleView(GroupRequiredMixin, CreateView):
    """Full page for direct navigation/no-JS; the candidate profile page's
    popup instead loads this same view's form as an HTML fragment (flagged by
    the X-Requested-With header) and, on a valid save, gets back the
    invite-email draft fragment instead of a redirect.

    Also reachable with a `request_pk` (see the Reschedule popup's own
    "Manual Slot Allocate" escape hatch, `interview_request_manual_allocate`)
    to resolve that existing, still-open InterviewRequest directly into this
    new Interview - skipping the interviewer's own slot-proposal round trip
    entirely - instead of leaving it dangling as a competing "open request"
    that would otherwise block this exact save."""
    model = Interview
    form_class = InterviewForm
    template_name = 'interviews/interview_form.html'
    allowed_groups = (HR_ADMIN, RECRUITER, HIRING_MANAGER)

    def dispatch(self, request, *args, **kwargs):
        self.candidate = get_object_or_404(Candidate, pk=kwargs['candidate_id'])
        self.interview_request = None
        request_pk = kwargs.get('request_pk')
        if request_pk:
            self.interview_request = get_object_or_404(
                InterviewRequest, pk=request_pk, candidate=self.candidate)
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, *args, **kwargs):
        # Turn the request away before the form is even shown, so HR sees why
        # straight away instead of filling it in and being rejected on save.
        # (The same rule is enforced on POST by InterviewForm.clean.)
        open_interview = Interview.open_for(self.candidate).first()
        if open_interview:
            if _is_ajax(request):
                # The modal's loader always expects an HTML fragment - keep
                # this consistent with every other AJAX response here.
                return HttpResponse(format_html(
                    '<div class="alert alert-danger mb-0">{}</div>',
                    open_interview_message(open_interview)))
            messages.error(request, open_interview_message(open_interview))
            return redirect('candidate_timeline', pk=self.candidate.pk)
        return super().get(request, *args, **kwargs)

    def get_template_names(self):
        if _is_ajax(self.request):
            return ['interviews/_schedule_form.html']
        return [self.template_name]

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['candidate'] = self.candidate
        kwargs['resolving_request'] = self.interview_request
        return kwargs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['candidate'] = self.candidate
        ctx['back_url'] = reverse('candidate_timeline', args=[self.candidate.pk])
        ctx['back_label'] = self.candidate.full_name
        return ctx

    def form_valid(self, form):
        form.instance.candidate = self.candidate
        form.instance.created_by = self.request.user
        response = super().form_valid(form)
        if self.interview_request:
            self.interview_request.status = InterviewRequest.Status.SCHEDULED
            self.interview_request.interview = self.object
            self.interview_request.save(update_fields=['status', 'interview'])
            # HR set the time by hand, so the slot the candidate may have
            # picked (and blocked) no longer applies.
            calendar_sync.cancel_request_event(self.interview_request, 'HR scheduled this interview directly.')
        calendar_sync.sync_interview(self.object, _portal_url(self.request, self.candidate.pk))
        _maybe_create_teams_meeting(self.object)
        if _is_ajax(self.request):
            return _invite_draft_response(self.request, self.object, form.cleaned_data.get('candidate_email'))
        messages.success(self.request, f'Interview scheduled for {self.candidate.full_name}.')
        return response

    def form_invalid(self, form):
        response = super().form_invalid(form)
        if _is_ajax(self.request):
            response.status_code = 400
        return response

    def get_success_url(self):
        return reverse('candidate_timeline', args=[self.candidate.pk])


class InterviewAllocateView(GroupRequiredMixin, CreateView):
    """Step 1 of the interviewer-proposes-slots flow: HR only picks an
    interviewer (no date) - the interview itself doesn't exist yet, just this
    InterviewRequest. Same full-page/AJAX-fragment dual pattern as
    InterviewScheduleView."""
    model = InterviewRequest
    form_class = InterviewAllocationForm
    template_name = 'interviews/interview_allocate_form.html'
    allowed_groups = (HR_ADMIN, RECRUITER, HIRING_MANAGER)

    def dispatch(self, request, *args, **kwargs):
        self.candidate = get_object_or_404(Candidate, pk=kwargs['candidate_id'])
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, *args, **kwargs):
        open_interview = Interview.open_for(self.candidate).first()
        open_request = InterviewRequest.open_for(self.candidate).first()
        blocker = open_interview and open_interview_message(open_interview) or \
            open_request and open_interview_request_message(open_request)
        if blocker:
            if _is_ajax(request):
                return HttpResponse(format_html('<div class="alert alert-danger mb-0">{}</div>', blocker))
            messages.error(request, blocker)
            return redirect('candidate_timeline', pk=self.candidate.pk)
        return super().get(request, *args, **kwargs)

    def get_template_names(self):
        if _is_ajax(self.request):
            return ['interviews/_allocate_form.html']
        return [self.template_name]

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['candidate'] = self.candidate
        return kwargs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['candidate'] = self.candidate
        ctx['back_url'] = reverse('candidate_timeline', args=[self.candidate.pk])
        ctx['back_label'] = self.candidate.full_name
        return ctx

    def form_valid(self, form):
        form.instance.candidate = self.candidate
        form.instance.created_by = self.request.user
        response = super().form_valid(form)
        notifications.notify(
            self.object.interviewer, title=f'New interview to schedule - {self.candidate.full_name}',
            message=f'Propose 2-3 one-hour slots for {self.candidate.full_name} '
                    f'({self.object.get_round_type_display()}).',
            url=reverse('interviewer_propose_slots', args=[self.object.pk]))
        try:
            slot_emails.notify_interviewer_new_request(
                self.object, login_url=self.request.build_absolute_uri(reverse('interviewer_login')))
        except logic_app_mail.EmailSendError as exc:
            logger.warning('Could not email the new-allocation notice for request %s: %s', self.object.pk, exc)
        if _is_ajax(self.request):
            return HttpResponse(format_html(
                '<div class="alert alert-success mb-0">Interviewer allocated - {} will propose available slots.</div>',
                self.object.interviewer.get_full_name() or self.object.interviewer.get_username()))
        messages.success(self.request, f'Interviewer allocated for {self.candidate.full_name}.')
        return response

    def form_invalid(self, form):
        response = super().form_invalid(form)
        if _is_ajax(self.request):
            response.status_code = 400
        return response

    def get_success_url(self):
        return reverse('candidate_timeline', args=[self.candidate.pk])


class InterviewRequestApproveView(GroupRequiredMixin, View):
    """HR approves the slot the candidate picked. This is what actually
    creates the Interview row - everything after this point (invite
    review/send, results, reschedule) is the existing flow, untouched.

    The stage card opens this in the interview popup: GET is a one-step
    confirmation, and the AJAX POST answers with the editable invite draft
    (_invite_draft_response) - so HR always reviews the candidate's email
    before it goes, and nothing is sent until they press Send Invite."""
    allowed_groups = (HR_ADMIN, RECRUITER, HIRING_MANAGER)

    def get(self, request, pk):
        request_obj = get_object_or_404(
            InterviewRequest.objects.select_related('candidate', 'interviewer', 'candidate_selected_slot'),
            pk=pk, status=InterviewRequest.Status.AWAITING_HR_APPROVAL)
        if not _is_ajax(request):
            return redirect('candidate_timeline', pk=request_obj.candidate_id)
        return HttpResponse(render_to_string(
            'interviews/_approve_confirm.html', {'interview_request': request_obj}, request=request))

    def post(self, request, pk):
        request_obj = get_object_or_404(
            InterviewRequest, pk=pk, status=InterviewRequest.Status.AWAITING_HR_APPROVAL)
        slot = request_obj.candidate_selected_slot
        if not slot:
            messages.error(request, 'No selected slot to approve.')
            return redirect('candidate_timeline', pk=request_obj.candidate_id)

        clash = Interview.conflicts_for(request_obj.interviewer, slot.start_datetime).first()
        if clash:
            if _is_ajax(request):
                return HttpResponse(format_html(
                    '<div class="alert alert-danger mb-0">{}</div>', interviewer_conflict_message(clash)), status=400)
            messages.error(request, interviewer_conflict_message(clash))
            return redirect('candidate_timeline', pk=request_obj.candidate_id)

        interview = Interview.objects.create(
            candidate=request_obj.candidate, round_type=request_obj.round_type,
            interviewer=request_obj.interviewer, scheduled_date=slot.start_datetime,
            mode=request_obj.mode, created_by=request.user,
        )
        request_obj.status = InterviewRequest.Status.SCHEDULED
        request_obj.interview = interview
        request_obj.save(update_fields=['status', 'interview'])
        # The meeting blocked when the candidate picked is reused as-is; only
        # if that never happened (calendar off/failed then) is one made now.
        if not calendar_sync.adopt_request_event(interview, request_obj):
            calendar_sync.sync_interview(interview, _portal_url(request, request_obj.candidate_id))
        _maybe_create_teams_meeting(interview)
        if _is_ajax(request):
            return _invite_draft_response(request, interview, None)
        messages.success(request, f'Interview approved for {request_obj.candidate.full_name}.')
        return redirect('candidate_timeline', pk=request_obj.candidate_id)


class InterviewRequestRescheduleView(GroupRequiredMixin, UpdateView):
    """Reschedule popup on the Round 1/2 stage card, reached once the
    candidate has already picked a slot - mirrors Allocate Interviewer's own
    form (round type/interviewer/mode), bound to this SAME InterviewRequest
    instead of creating a new one, so HR can change interviewer/mode instead
    of being stuck re-asking the one already allocated. Saving resets this
    request back to Awaiting Slots (clearing any proposed/picked slots and
    the candidate's now-stale link) and notifies whichever interviewer ends
    up assigned to propose fresh slots - exactly like a brand new
    allocation. The fragment's own "Manual Slot Allocate" escape hatch
    instead resolves this same request directly via
    InterviewScheduleView(request_pk=...), skipping this reset entirely."""
    model = InterviewRequest
    form_class = InterviewAllocationForm
    template_name = 'interviews/interview_allocate_form.html'
    allowed_groups = (HR_ADMIN, RECRUITER, HIRING_MANAGER)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['candidate'] = self.object.candidate
        return kwargs

    def get_template_names(self):
        if _is_ajax(self.request):
            return ['interviews/_reschedule_request_form.html']
        return [self.template_name]

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['candidate'] = self.object.candidate
        ctx['back_url'] = reverse('candidate_timeline', args=[self.object.candidate_id])
        ctx['back_label'] = self.object.candidate.full_name
        return ctx

    def form_valid(self, form):
        super().form_valid(form)
        request_obj = self.object
        calendar_sync.cancel_request_event(request_obj, 'HR is rescheduling this interview.')
        request_obj.slots.all().delete()
        request_obj.status = InterviewRequest.Status.AWAITING_SLOTS
        request_obj.candidate_token = None
        request_obj.slots_proposed_at = None
        request_obj.candidate_selected_slot = None
        request_obj.candidate_selected_at = None
        request_obj.save(update_fields=[
            'status', 'candidate_token', 'slots_proposed_at', 'candidate_selected_slot', 'candidate_selected_at'])
        interviewer_name = request_obj.interviewer.get_full_name() or request_obj.interviewer.get_username()
        notifications.notify(
            request_obj.interviewer, title=f'New interview to schedule - {request_obj.candidate.full_name}',
            message=f'Propose 2-3 one-hour slots for {request_obj.candidate.full_name} '
                    f'({request_obj.get_round_type_display()}).',
            url=reverse('interviewer_propose_slots', args=[request_obj.pk]))
        try:
            slot_emails.notify_interviewer_new_request(
                request_obj, login_url=self.request.build_absolute_uri(reverse('interviewer_login')))
        except logic_app_mail.EmailSendError as exc:
            logger.warning('Could not email the reschedule notice for request %s: %s', request_obj.pk, exc)
        if _is_ajax(self.request):
            return HttpResponse(format_html(
                '<div class="alert alert-success mb-0">Interview reset - {} will propose available slots.</div>',
                interviewer_name))
        messages.success(self.request, f'Interview reset for {request_obj.candidate.full_name} - '
                                       f'asked {interviewer_name} to propose slots.')
        return redirect('candidate_timeline', pk=request_obj.candidate_id)

    def form_invalid(self, form):
        response = super().form_invalid(form)
        if _is_ajax(self.request):
            response.status_code = 400
        return response

    def get_success_url(self):
        return reverse('candidate_timeline', args=[self.object.candidate_id])


class InterviewRequestNewSlotsView(GroupRequiredMixin, View):
    """HR isn't happy with any of the proposed slots, or wants to reschedule
    after the candidate already picked one - clears the slots/pick and sends
    the interviewer back to propose a fresh set."""
    allowed_groups = (HR_ADMIN, RECRUITER, HIRING_MANAGER)

    def post(self, request, pk):
        request_obj = get_object_or_404(
            InterviewRequest, pk=pk,
            status__in=(InterviewRequest.Status.AWAITING_SELECTION, InterviewRequest.Status.AWAITING_HR_APPROVAL))
        calendar_sync.cancel_request_event(request_obj, 'HR asked for a different time.')
        request_obj.slots.all().delete()
        request_obj.status = InterviewRequest.Status.AWAITING_SLOTS
        request_obj.candidate_token = None
        request_obj.slots_proposed_at = None
        request_obj.candidate_selected_slot = None
        request_obj.candidate_selected_at = None
        request_obj.save(update_fields=[
            'status', 'candidate_token', 'slots_proposed_at', 'candidate_selected_slot', 'candidate_selected_at'])
        note = (request.POST.get('note') or '').strip()
        notifications.notify(
            request_obj.interviewer, title=f'New slots needed - {request_obj.candidate.full_name}',
            message=note or 'Please propose a fresh set of slots.',
            url=reverse('interviewer_propose_slots', args=[request_obj.pk]))
        try:
            slot_emails.notify_interviewer_new_slots_needed(
                request_obj, note=note, login_url=request.build_absolute_uri(reverse('interviewer_login')))
        except logic_app_mail.EmailSendError as exc:
            logger.warning('Could not email the new-slots-needed notice for request %s: %s', request_obj.pk, exc)
        messages.success(request, 'Asked the interviewer to propose new slots.')
        return redirect('candidate_timeline', pk=request_obj.candidate_id)


class InterviewRequestCancelView(GroupRequiredMixin, View):
    """Cancels an interview that HR allocated but hasn't scheduled yet - while
    waiting on the interviewer's slots, the candidate's pick, or HR's own
    approval. The candidate's pick link stops working, any calendar block is
    withdrawn, and the interviewer is told. The round's card then offers
    Allocate Interviewer again, as if nothing had been allocated. (An
    already-scheduled interview is cancelled by InterviewCancelView.)"""
    allowed_groups = (HR_ADMIN, RECRUITER, HIRING_MANAGER)

    def post(self, request, pk):
        request_obj = get_object_or_404(
            InterviewRequest.objects.select_related('candidate', 'interviewer', 'candidate_selected_slot'),
            pk=pk, status__in=InterviewRequest.OPEN_STATUSES)
        candidate = request_obj.candidate
        slot = request_obj.candidate_selected_slot
        calendar_sync.cancel_request_event(request_obj, 'This interview was cancelled by HR.')
        request_obj.status = InterviewRequest.Status.CANCELLED
        request_obj.candidate_token = None
        request_obj.save(update_fields=['status', 'candidate_token'])
        note = (request.POST.get('note') or '').strip()
        _tell_interviewer_cancelled(
            request_obj.interviewer, candidate, request_obj.get_round_type_display(),
            when=slot.start_datetime if slot else None, note=note)
        messages.success(request, f'{request_obj.get_round_type_display()} interview cancelled for '
                                  f'{candidate.full_name} - the interviewer has been told.')
        return redirect('candidate_timeline', pk=candidate.pk)


def _maybe_create_teams_meeting(interview):
    """Phase 2: fill meeting_link with a real Teams join URL when Graph is
    configured, HR hasn't already pasted one in, and this is a video
    interview. Best-effort - a Graph outage just leaves meeting_link blank,
    same as before Phase 2 existed."""
    if (not graph_client.is_configured() or interview.meeting_link
            or interview.mode != Interview.Mode.VIDEO):
        return
    try:
        interview.meeting_link = graph_client.create_online_meeting(
            subject=f'Interview - {interview.candidate.full_name}',
            start=interview.scheduled_date, end=interview.scheduled_date + INTERVIEW_DURATION,
        )
    except graph_client.GraphError as exc:
        logger.warning('Could not auto-create a Teams meeting for interview %s: %s', interview.pk, exc)
        return
    interview.save(update_fields=['meeting_link'])


def _invite_draft_response(request, interview, to_email):
    """The step-2 fragment: an editable invite email draft for the interview
    just scheduled/rescheduled, rendered with real values (candidate name,
    role, date, sender)."""
    draft = {
        'to_email': (to_email or interview.candidate.email or '').strip(),
        'cc_list': invites.default_cc_list(interview),
        'subject': invites.default_subject(interview),
        'body': invites.default_body(interview, request.user),
    }
    html = render_to_string('interviews/_invite_draft.html', {
        'interview': interview, 'draft': draft,
    }, request=request)
    return HttpResponse(html)


def _same_minute(a, b):
    """The date picker only offers minutes, so anything finer that a row happens
    to carry (imported rows can have seconds) is not a move the user made."""
    return a.replace(second=0, microsecond=0) == b.replace(second=0, microsecond=0)


class InterviewRescheduleView(GroupRequiredMixin, UpdateView):
    """See InterviewScheduleView's docstring - same full-page/AJAX-fragment
    dual behaviour."""
    model = Interview
    form_class = InterviewForm
    template_name = 'interviews/interview_form.html'
    allowed_groups = (HR_ADMIN, RECRUITER, HIRING_MANAGER)

    def get_object(self, queryset=None):
        obj = super().get_object(queryset)
        # Snapshot before the form writes over it - the row is rewritten in
        # place, so this is the only chance to see what it used to say.
        self.before = {'scheduled_date': obj.scheduled_date, 'interviewer_id': obj.interviewer_id}
        return obj

    def get_template_names(self):
        if _is_ajax(self.request):
            return ['interviews/_schedule_form.html']
        return [self.template_name]

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['candidate'] = self.object.candidate
        ctx['back_url'] = reverse('candidate_timeline', args=[self.object.candidate_id])
        ctx['back_label'] = self.object.candidate.full_name
        return ctx

    def form_valid(self, form):
        moved = not _same_minute(form.instance.scheduled_date, self.before['scheduled_date'])
        reassigned = form.instance.interviewer_id != self.before['interviewer_id']
        if moved:
            form.instance.status = Interview.Status.RESCHEDULED
        response = super().form_valid(form)
        if moved or reassigned:
            InterviewReschedule.objects.create(
                interview=self.object,
                previous_date=self.before['scheduled_date'], new_date=self.object.scheduled_date,
                previous_interviewer_id=self.before['interviewer_id'],
                new_interviewer_id=self.object.interviewer_id,
                changed_by=self.request.user)
            calendar_sync.sync_interview(self.object, _portal_url(self.request, self.object.candidate_id))
        _maybe_create_teams_meeting(self.object)
        if _is_ajax(self.request):
            return _invite_draft_response(self.request, self.object, form.cleaned_data.get('candidate_email'))
        messages.success(self.request, 'Interview rescheduled.' if (moved or reassigned) else 'Interview updated.')
        return response

    def form_invalid(self, form):
        response = super().form_invalid(form)
        if _is_ajax(self.request):
            response.status_code = 400
        return response

    def get_success_url(self):
        return reverse('candidate_timeline', args=[self.object.candidate_id])


class InterviewMarkDoneView(GroupRequiredMixin, View):
    """Marks an interview attended without committing to a result yet - the
    Hiring block's Round 1/Round 2 Schedule card offers this separately from
    the pass/fail decision, which the candidate's next stage card (Cleared/
    Hold/Reject/Blacklist) makes instead - see CandidateStatusActionView,
    which settles this interview's result to match once that decision lands."""
    allowed_groups = (HR_ADMIN, RECRUITER, HIRING_MANAGER)

    def post(self, request, pk):
        interview = get_object_or_404(Interview, pk=pk)
        interview.status = Interview.Status.COMPLETED
        interview.save(update_fields=['status'])
        return redirect('candidate_timeline', pk=interview.candidate_id)


class InterviewCancelView(GroupRequiredMixin, View):
    """Marks an interview cancelled/no-show. Not a result (pass/fail) - the
    Hiring block prompts for Reject or Hold once an interview is cancelled,
    rather than silently leaving the candidate stuck at this stage.

    Cancelling one that hasn't happened yet also withdraws the meeting from
    the interviewer's calendar and tells them; a past one is just a no-show
    being recorded, so the interviewer isn't messaged about it."""
    allowed_groups = (HR_ADMIN, RECRUITER, HIRING_MANAGER)

    def post(self, request, pk):
        interview = get_object_or_404(Interview.objects.select_related('candidate', 'interviewer'), pk=pk)
        upcoming = interview.scheduled_date > timezone.now()
        interview.status = Interview.Status.CANCELLED
        interview.cancelled_at = timezone.now()
        interview.save(update_fields=['status', 'cancelled_at'])
        if upcoming:
            calendar_sync.cancel_interview_event(interview, 'This interview was cancelled by HR.')
            _tell_interviewer_cancelled(
                interview.interviewer, interview.candidate, interview.get_round_type_display(),
                when=interview.scheduled_date, note=(request.POST.get('note') or '').strip())
        return redirect('candidate_timeline', pk=interview.candidate_id)


class InterviewResultView(GroupRequiredMixin, UpdateView):
    """Reachable by whoever can act on this interview: HR/Recruiter/Hiring
    Manager (same as the Interview Scheduler's own "Update Status" link,
    which every ANY_STAFF role can see - RECRUITER/HIRING_MANAGER were
    missing here before, a pre-existing gap that made that link 403 for
    them) and Interviewer, via the Interviewer portal."""
    model = Interview
    form_class = InterviewResultForm
    template_name = 'interviews/interview_result_form.html'
    allowed_groups = (HR_ADMIN, RECRUITER, HIRING_MANAGER, INTERVIEWER)

    # On Pass, advance the candidate one stage down the pipeline rather than
    # hiring outright: Round 1 -> Interview (Round 2) -> Final Selection, where HR
    # makes the actual hire decision. Anything not listed falls back to Hired.
    PASS_NEXT = {
        Candidate.Status.SHORTLISTED: Candidate.Status.ROUND1,
        Candidate.Status.ROUND1: Candidate.Status.INTERVIEW,
        Candidate.Status.INTERVIEW: Candidate.Status.FINAL_SELECTION,
        Candidate.Status.FINAL_SELECTION: Candidate.Status.HIRED,
    }

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['breadcrumb_current'] = f'{self.object.candidate.full_name} — {self.object.get_round_type_display()}'
        # Reached from the interviewer portal too (allowed_groups above) -
        # Cancel/breadcrumbs need to point back there instead of the main HR
        # candidate page whenever this session is in portal mode (a plain
        # Interviewer, or an Admin who signed in via the Interviewer login).
        if in_interviewer_portal(self.request):
            ctx['cancel_url'] = reverse('interviewer_candidate', args=[self.object.candidate_id])
            ctx['back_url'] = ctx['cancel_url']
            ctx['back_label'] = self.object.candidate.full_name
        else:
            ctx['cancel_url'] = reverse('candidate_timeline', args=[self.object.candidate_id])
        return ctx

    def _can_decide_pipeline(self):
        """Whether this submitter's Pass/Fail is the final word (HR/Recruiter/
        Hiring Manager) or just a recommendation for HR to confirm
        (Interviewer) - see form_valid."""
        user = self.request.user
        return user.is_superuser or user.groups.filter(name__in=(HR_ADMIN, RECRUITER, HIRING_MANAGER)).exists()

    def get_queryset(self):
        # HR/Recruiter/Hiring Manager can record a result on any interview;
        # a plain Interviewer (allowed_groups includes INTERVIEWER, for the
        # portal) must be scoped to interviews actually assigned to them -
        # otherwise changing the pk in the URL would let one interviewer
        # read and overwrite another's feedback/result.
        qs = super().get_queryset()
        if not self._can_decide_pipeline():
            qs = qs.filter(interviewer=self.request.user)
        return qs

    def form_valid(self, form):
        # Marking a result always completes the interview ("Round status
        # automatically updates as Done").
        form.instance.status = Interview.Status.COMPLETED
        can_decide_pipeline = self._can_decide_pipeline()
        if not can_decide_pipeline:
            # An Interviewer's Pass/Fail is a recommendation, not the final
            # call - the candidate's stage only moves once HR/Recruiter
            # decides via the Hiring block's Cleared/Hold/Reject. Keep the
            # interview's own result Pending (exactly like the ordinary
            # "Mark Done" flow - see InterviewMarkDoneView) so that block's
            # decision phase picks this interview up correctly and folds
            # their pick into the feedback HR will see pre-filled there
            # (candidates/templates/candidates/timeline.html's Round 1/2
            # remarks box), instead of it silently deciding the outcome.
            picked = form.cleaned_data.get('result')
            recommendation = {
                Interview.Result.PASS_: 'Recommended: Move to Next Round.',
                Interview.Result.FAIL: 'Recommended: Reject.',
                Interview.Result.HOLD: 'Recommended: Hold.',
            }.get(picked)
            if recommendation:
                feedback = (form.cleaned_data.get('feedback') or '').strip()
                form.instance.feedback = f'{recommendation} {feedback}'.strip()
            form.instance.result = Interview.Result.PENDING
        response = super().form_valid(form)
        interview = self.object
        candidate = interview.candidate
        # Marking any result (Move to Next Round/Reject/Hold) means the
        # candidate actually attended the interview - log it automatically
        # instead of requiring a separate manual Interview Communication Log
        # entry (see timeline.html's Round 1/2 stage cards).
        CommunicationLog.objects.create(
            candidate=candidate, channel=CommunicationLog.Channel.INTERVIEW,
            outcome=CommunicationLog.Outcome.ATTENDED, logged_by=self.request.user)
        if not can_decide_pipeline:
            self._tell_hr_result_recorded(interview, form.cleaned_data.get('result'),
                                          (form.cleaned_data.get('feedback') or '').strip())
            messages.success(self.request, 'Result recorded - HR/Recruiter will confirm the next step.')
            return response
        performed_by = self.request.user.get_full_name() or self.request.user.get_username()
        round_label = interview.get_round_type_display()
        feedback = (interview.feedback or '').strip()

        def remark(text):
            return f'{text} {feedback}'.strip()
        if interview.result == Interview.Result.PASS_:
            next_status = self.PASS_NEXT.get(candidate.status, Candidate.Status.HIRED)
            services.change_status(candidate, next_status, user=self.request.user,
                                   remarks=remark(f'{round_label} interview passed.'), performed_by=performed_by)
            messages.success(self.request, f'{candidate.full_name} passed — moved to "{candidate.status_label}".')
        elif interview.result == Interview.Result.FAIL:
            services.change_status(candidate, Candidate.Status.REJECTED, user=self.request.user,
                                   remarks=remark(f'{round_label} interview failed.'), performed_by=performed_by)
            messages.success(self.request, f'{candidate.full_name} failed and was moved to Rejected.')
        elif interview.result == Interview.Result.HOLD:
            # Hold pauses the candidate - it doesn't decide the interview, so
            # it's put back to Pending rather than saved as Hold (matches the
            # Hiring block's own Hold action - see
            # candidates.views._settle_round_interview) so the round's
            # decision phase, with this feedback, is still there once the
            # candidate resumes.
            interview.result = Interview.Result.PENDING
            interview.save(update_fields=['result'])
            services.change_status(candidate, Candidate.Status.SCREENING_HOLD, user=self.request.user,
                                   remarks=remark(f'{round_label} interview put on hold.'), performed_by=performed_by)
            messages.success(self.request, f'{candidate.full_name} moved to Hold.')
        else:
            messages.success(self.request, 'Interview result recorded.')
        return response

    def _tell_hr_result_recorded(self, interview, picked, feedback):
        """An Interviewer's result is a recommendation HR has to confirm -
        tell whoever allocated the interviewer and whoever scheduled the
        interview (in-app + email). Best-effort: a failed email never
        blocks recording the result."""
        allocation = InterviewRequest.objects.filter(interview=interview).select_related('created_by').first()
        hr_users = [u for u in dict.fromkeys(
            (allocation.created_by if allocation else None, interview.created_by)) if u]
        recommendation = Interview.Result(picked).label if picked else 'No recommendation'
        candidate = interview.candidate
        profile_url = self.request.build_absolute_uri(reverse('candidate_timeline', args=[candidate.pk]))
        for user in hr_users:
            notifications.notify(
                user, title=f'Interview result - {candidate.full_name}',
                message=f'{interview.get_round_type_display()}: {recommendation}. '
                        f'Confirm the next step on the candidate\'s profile.',
                url=reverse('candidate_timeline', args=[candidate.pk]))
        try:
            slot_emails.notify_hr_result_recorded(
                hr_users=hr_users, interview=interview, recommendation=recommendation,
                feedback=feedback, profile_url=profile_url)
        except logic_app_mail.EmailSendError as exc:
            logger.warning('Could not email HR the result of interview %s: %s', interview.pk, exc)

    def get_success_url(self):
        if in_interviewer_portal(self.request):
            return reverse('interviewer_home')
        return reverse('interview_scheduler')


class InterviewSendInviteView(GroupRequiredMixin, View):
    """Sends the interview invite - subject/body/recipient come from what HR
    reviewed and possibly edited in the popup; CC is always derived
    server-side (fixed HR address + the selected interviewer) so it can't be
    tampered with or accidentally dropped. Includes a .ics attachment so it
    lands in Outlook as a real meeting, not just a plain email.

    Console backend in dev (see settings.EMAIL_BACKEND) - invites print to
    the log instead of sending until real SMTP credentials are configured.

    GET is the same review form as a full page - what the Interview
    Scheduler list links to - so an invite is never sent unseen."""
    allowed_groups = (HR_ADMIN, RECRUITER, HIRING_MANAGER)

    def get(self, request, pk):
        interview = get_object_or_404(Interview.objects.select_related('candidate', 'interviewer'), pk=pk)
        back_url = request.GET.get('next') or reverse('interview_scheduler')
        if not url_has_allowed_host_and_scheme(back_url, allowed_hosts={request.get_host()}):
            back_url = reverse('interview_scheduler')
        draft = {
            'to_email': (interview.candidate.email or '').strip(),
            'cc_list': invites.default_cc_list(interview),
            'subject': invites.default_subject(interview),
            'body': invites.default_body(interview, request.user),
        }
        return render(request, 'interviews/invite_review.html', {
            'interview': interview, 'draft': draft, 'standalone': True,
            'back_url': back_url, 'back_label': 'Interview Scheduler',
        })

    def post(self, request, pk):
        interview = get_object_or_404(Interview, pk=pk)
        candidate = interview.candidate
        is_ajax = _is_ajax(request)
        # The scheduler list page's "Send Invite" button posts none of these -
        # just a bare CSRF token - so every one of them needs a default, not
        # just subject.
        to_email = (request.POST.get('to_email') or candidate.email or '').strip()
        subject = request.POST.get('subject', '').strip() or invites.default_subject(interview)
        body = request.POST.get('body', '').strip() or invites.default_body(interview, request.user)

        error = None
        if not to_email:
            error = 'No candidate email to send the invite to.'
        else:
            cc_list = invites.default_cc_list(interview)
            try:
                invites.send_invite(interview, to_email=to_email, cc_emails=cc_list,
                                    subject=subject, body=body, sender=request.user)
            except Exception as exc:  # noqa: BLE001 - surfaced to the HR user, not swallowed
                logger.exception('Failed to send interview invite for interview %s', interview.pk)
                error = f'Could not send the invite: {exc}'
            else:
                Note.objects.create(
                    candidate=candidate, author=request.user,
                    text=f'Interview invite emailed to {to_email}'
                         f'{" (cc: " + ", ".join(cc_list) + ")" if cc_list else ""}.')

        if error:
            if is_ajax:
                return JsonResponse({'ok': False, 'error': error}, status=502)
            messages.error(request, error)
            return redirect('candidate_timeline', pk=candidate.pk)

        if is_ajax:
            return JsonResponse({'ok': True})
        messages.success(request, f'Invite sent to {to_email}.')
        return redirect('candidate_timeline', pk=candidate.pk)
