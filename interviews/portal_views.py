"""The Interviewer portal: a small, read-mostly area confined to exactly what
an interviewer needs - the interviews assigned to them, the candidate on each
one, and recording a result (interviews.views.InterviewResultView, reused
as-is). Everything else in the app (dashboard, candidate repository, job
management, the full interview scheduler, ...) is off-limits to this role -
see candidates.permissions.ANY_STAFF, which deliberately excludes Interviewer.
"""
import logging

from django.contrib import messages
from django.contrib.auth import views as auth_views
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.generic import DetailView, ListView

from notifications import services as notifications

from HR_management import azure_auth
from candidates import logic_app_mail
from candidates.models import Candidate
from candidates.permissions import HR_ADMIN, INTERVIEWER, PORTAL_SESSION_KEY, GroupRequiredMixin

from . import slot_emails
from .forms import InterviewSlotProposalForm
from .models import Interview, InterviewRequest, InterviewSlot

logger = logging.getLogger(__name__)


def _is_admin(user):
    """Admin (HR_ADMIN) or superuser - allowed through the portal's login
    gate alongside Interviewer (see InterviewerLoginView.form_valid). Once
    inside, an Admin is scoped exactly like any other interviewer - HR Admin
    can now be assigned as an interviewer themselves (interviews/forms.py),
    so "every interview, every interviewer" here would show interviews that
    aren't theirs. Browsing everyone's is still available via the full HR
    Interview Scheduler, which Admin already has unrestricted access to."""
    return user.is_superuser or user.groups.filter(name=HR_ADMIN).exists()


class InterviewerLoginView(auth_views.LoginView):
    """A separate front door from the main HR sign-in (registration/login.html),
    for the link shared specifically with interviewers. Authenticates the
    same way, but refuses anyone who isn't actually an Interviewer or Admin
    (or a superuser) - they're pointed at the HR sign-in instead."""
    template_name = 'registration/interviewer_login.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['azure_login_available'] = azure_auth.is_configured()
        return ctx

    def get_success_url(self):
        return reverse('interviewer_home')

    def form_valid(self, form):
        user = form.get_user()
        if not (_is_admin(user) or user.groups.filter(name=INTERVIEWER).exists()):
            form.add_error(None, 'This sign-in is for interviewers only. Use the HR sign-in instead.')
            return self.form_invalid(form)
        response = super().form_valid(form)
        # Marks the whole session as "portal mode" - see
        # candidates.permissions.in_interviewer_portal - so an Admin using
        # this door gets the same restricted nav/back-links an Interviewer
        # does, for as long as this session lasts (cleared by logging in via
        # the main HR login instead - HR_management.auth_views.HRLoginView).
        self.request.session[PORTAL_SESSION_KEY] = True
        return response


class InterviewerHomeView(GroupRequiredMixin, ListView):
    """Landing page after an interviewer signs in, as 3 tabs: Prospects
    (interviewer-proposes-slots requests still in progress), Scheduled
    (upcoming interviews) and Result Pending (the slot has passed but no
    result recorded yet). Always scoped to the signed-in user's own
    interviews, including for an Admin - see _is_admin's docstring."""
    model = Interview
    template_name = 'interviews/portal_home.html'
    context_object_name = 'interviews'
    allowed_groups = (INTERVIEWER, HR_ADMIN)

    def get_queryset(self):
        # Only interviews still awaiting their result belong on this landing
        # page - once a result is recorded (or the interview is cancelled)
        # it drops off entirely; that history lives on the candidate's own
        # timeline instead.
        return (Interview.objects.filter(
                    status__in=Interview.OPEN_STATUSES, interviewer=self.request.user)
                .select_related('candidate', 'candidate__job', 'interviewer')
                .order_by('scheduled_date'))

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        # Scheduled: still ahead of us. Result Pending: the slot has passed
        # but nothing's been recorded yet - the interviewer needs to act.
        now = timezone.now()
        interviews = list(ctx['interviews'])
        ctx['scheduled'] = [i for i in interviews if i.scheduled_date > now]
        ctx['result_pending'] = [i for i in interviews if i.scheduled_date <= now]

        ctx['next_prospects'] = (
            InterviewRequest.objects.filter(
                status__in=InterviewRequest.OPEN_STATUSES, interviewer=self.request.user)
            .select_related('candidate', 'candidate__job', 'interviewer')
            .order_by('-created_at'))
        return ctx


class InterviewerCandidateView(GroupRequiredMixin, DetailView):
    """A read-only candidate view scoped to exactly what helps an interviewer
    prepare - contact/background/education/experience, the CV, the AI
    summary - with none of the HR-only actions (edit/delete, status changes,
    hiring-stage progression, vacancy/source mapping). Restricted to
    candidates this interviewer actually has an interview with, OR is still
    just a Prospect for (an InterviewRequest allocated to them, before any
    slot is even scheduled - see the portal home's Prospects tab, which
    links here) - not the whole repository by ID - a 404, not just a group
    check, for anyone else. Applies the same way to an Admin using the
    portal - see _is_admin's docstring."""
    model = Candidate
    template_name = 'interviews/portal_candidate.html'
    context_object_name = 'candidate'
    allowed_groups = (INTERVIEWER, HR_ADMIN)

    def get_queryset(self):
        user = self.request.user
        return Candidate.objects.filter(
            Q(interviews__interviewer=user) | Q(interview_requests__interviewer=user)).distinct()

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['my_interviews'] = (
            self.object.interviews.filter(interviewer=self.request.user)
            .select_related('interviewer').order_by('-scheduled_date'))
        ctx['open_statuses'] = Interview.OPEN_STATUSES
        ctx['back_url'] = reverse('interviewer_home')
        ctx['back_label'] = 'My Interviews'
        # A later-round interviewer (e.g. Round 2) otherwise has no way to see
        # what an earlier round's interviewer wrote.
        ctx['other_round_feedback'] = (
            self.object.interviews.exclude(interviewer=self.request.user)
            .exclude(feedback__isnull=True).exclude(feedback='')
            .select_related('interviewer').order_by('scheduled_date'))
        return ctx


class InterviewProposeSlotsView(GroupRequiredMixin, View):
    """Step 2 of the interviewer-proposes-slots flow: the interviewer offers
    2-3 one-hour times for a candidate HR allocated to them. Scoped to their
    own requests only, including for an Admin - proposing slots on someone
    else's behalf isn't offered here."""
    allowed_groups = (INTERVIEWER, HR_ADMIN)
    template_name = 'interviews/portal_propose_slots.html'

    def _request_or_404(self, pk, user):
        qs = InterviewRequest.objects.filter(
            status=InterviewRequest.Status.AWAITING_SLOTS, interviewer=user)
        return get_object_or_404(qs, pk=pk)

    def get(self, request, pk):
        request_obj = self._request_or_404(pk, request.user)
        form = InterviewSlotProposalForm(interviewer=request_obj.interviewer)
        return self._render(request, request_obj, form)

    def post(self, request, pk):
        request_obj = self._request_or_404(pk, request.user)
        form = InterviewSlotProposalForm(request.POST, interviewer=request_obj.interviewer)
        if not form.is_valid():
            return self._render(request, request_obj, form, status=400)

        InterviewSlot.objects.bulk_create(
            InterviewSlot(request=request_obj, start_datetime=slot) for slot in form.cleaned_data['slots'])
        request_obj.status = InterviewRequest.Status.AWAITING_SELECTION
        request_obj.slots_proposed_at = timezone.now()
        request_obj.new_candidate_token()
        request_obj.save(update_fields=['status', 'slots_proposed_at', 'candidate_token'])
        candidate = request_obj.candidate
        if request_obj.created_by:
            notifications.notify(
                request_obj.created_by, title=f'Slots proposed - {candidate.full_name}',
                message=f'{request_obj.interviewer.get_full_name() or request_obj.interviewer.get_username()} '
                        f'proposed slots for {request_obj.get_round_type_display()} - '
                        f'{candidate.full_name} has been emailed to pick one.',
                url=reverse('candidate_timeline', args=[request_obj.candidate_id]))
        if candidate.email_is_placeholder:
            # No real email to send the picker link to - flag it instead of
            # silently going nowhere; HR will need to sort out contact info
            # or fall back to Manual Slot Allocate for this candidate.
            if request_obj.created_by:
                notifications.notify(
                    request_obj.created_by, title=f'No email on file - {candidate.full_name}',
                    message="Slots were proposed, but this candidate has no real email to send the "
                            "slot-pick link to. Update their contact details, or use Manual Slot "
                            "Allocate instead.",
                    url=reverse('candidate_timeline', args=[request_obj.candidate_id]))
        else:
            select_url = request.build_absolute_uri(
                reverse('candidate_slot_pick', args=[request_obj.candidate_token]))
            try:
                slot_emails.notify_candidate_select_slot(request_obj, select_url)
            except logic_app_mail.EmailSendError as exc:
                logger.warning('Could not email the slot-pick link for request %s: %s', request_obj.pk, exc)
        messages.success(request, f'Slots submitted - {candidate.full_name} will pick one to confirm the interview.')
        return redirect('interviewer_home')

    def _render(self, request, request_obj, form, status=200):
        return render(request, self.template_name, {
            'interview_request': request_obj, 'form': form,
            'back_url': reverse('interviewer_home'), 'back_label': 'My Interviews',
        }, status=status)
