"""The Interviewer portal: a small, read-mostly area confined to exactly what
an interviewer needs - the interviews assigned to them, the candidate on each
one, and recording a result (interviews.views.InterviewResultView, reused
as-is). Everything else in the app (dashboard, candidate repository, job
management, the full interview scheduler, ...) is off-limits to this role -
see candidates.permissions.ANY_STAFF, which deliberately excludes Interviewer.
"""
from django.contrib import messages
from django.contrib.auth import views as auth_views
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.generic import DetailView, ListView

from notifications import services as notifications

from candidates.models import Candidate
from candidates.permissions import HR_ADMIN, INTERVIEWER, PORTAL_SESSION_KEY, GroupRequiredMixin

from . import slot_emails
from .forms import InterviewSlotProposalForm
from .models import Interview, InterviewRequest, InterviewSlot


def _is_admin(user):
    """Admin (HR_ADMIN) or superuser - sees every interview/candidate in the
    portal instead of only their own, e.g. to check what an interviewer sees
    or to browse every scheduled interview from this simpler view instead of
    the full HR Interview Scheduler."""
    return user.is_superuser or user.groups.filter(name=HR_ADMIN).exists()


class InterviewerLoginView(auth_views.LoginView):
    """A separate front door from the main HR sign-in (registration/login.html),
    for the link shared specifically with interviewers. Authenticates the
    same way, but refuses anyone who isn't actually an Interviewer or Admin
    (or a superuser) - they're pointed at the HR sign-in instead."""
    template_name = 'registration/interviewer_login.html'

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
    result recorded yet). An Admin sees every interviewer's, instead of
    just their own."""
    model = Interview
    template_name = 'interviews/portal_home.html'
    context_object_name = 'interviews'
    allowed_groups = (INTERVIEWER, HR_ADMIN)

    def get_queryset(self):
        # Only interviews still awaiting their result belong on this landing
        # page - once a result is recorded (or the interview is cancelled)
        # it drops off entirely; that history lives on the candidate's own
        # timeline instead.
        qs = Interview.objects.filter(status__in=Interview.OPEN_STATUSES) \
            .select_related('candidate', 'candidate__job', 'interviewer')
        if not _is_admin(self.request.user):
            qs = qs.filter(interviewer=self.request.user)
        return qs.order_by('scheduled_date')

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        is_admin = _is_admin(self.request.user)
        ctx['is_admin_view'] = is_admin
        # Scheduled: still ahead of us. Result Pending: the slot has passed
        # but nothing's been recorded yet - the interviewer needs to act.
        now = timezone.now()
        interviews = list(ctx['interviews'])
        ctx['scheduled'] = [i for i in interviews if i.scheduled_date > now]
        ctx['result_pending'] = [i for i in interviews if i.scheduled_date <= now]

        requests_qs = InterviewRequest.objects.filter(status__in=InterviewRequest.OPEN_STATUSES) \
            .select_related('candidate', 'candidate__job', 'interviewer')
        if not is_admin:
            requests_qs = requests_qs.filter(interviewer=self.request.user)
        ctx['next_prospects'] = requests_qs.order_by('-created_at')
        return ctx


class InterviewerCandidateView(GroupRequiredMixin, DetailView):
    """A read-only candidate view scoped to exactly what helps an interviewer
    prepare - contact/background/education/experience, the CV, the AI
    summary - with none of the HR-only actions (edit/delete, status changes,
    hiring-stage progression, vacancy/source mapping). Restricted to
    candidates this interviewer actually has an interview with, not the whole
    repository by ID - a 404, not just a group check, for anyone else. An
    Admin can open any candidate that has an interview at all."""
    model = Candidate
    template_name = 'interviews/portal_candidate.html'
    context_object_name = 'candidate'
    allowed_groups = (INTERVIEWER, HR_ADMIN)

    def get_queryset(self):
        if _is_admin(self.request.user):
            return Candidate.objects.filter(interviews__isnull=False).distinct()
        return Candidate.objects.filter(interviews__interviewer=self.request.user).distinct()

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        is_admin = _is_admin(self.request.user)
        interviews_qs = self.object.interviews.all() if is_admin else (
            self.object.interviews.filter(interviewer=self.request.user))
        ctx['my_interviews'] = interviews_qs.select_related('interviewer').order_by('-scheduled_date')
        ctx['is_admin_view'] = is_admin
        ctx['open_statuses'] = Interview.OPEN_STATUSES
        ctx['back_url'] = reverse('interviewer_home')
        ctx['back_label'] = 'My Interviews'
        return ctx


class InterviewProposeSlotsView(GroupRequiredMixin, View):
    """Step 2 of the interviewer-proposes-slots flow: the interviewer offers
    2-3 one-hour times for a candidate HR allocated to them. Scoped to their
    own requests only (an Admin using the portal can see every request via
    InterviewerHomeView.next_prospects, but proposing slots on someone else's
    behalf isn't offered here)."""
    allowed_groups = (INTERVIEWER, HR_ADMIN)
    template_name = 'interviews/portal_propose_slots.html'

    def _request_or_404(self, pk, user):
        qs = InterviewRequest.objects.filter(status=InterviewRequest.Status.AWAITING_SLOTS)
        if not _is_admin(user):
            qs = qs.filter(interviewer=user)
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
        request_obj.save(update_fields=['status'])
        if request_obj.created_by:
            notifications.notify(
                request_obj.created_by, title=f'Slots proposed - {request_obj.candidate.full_name}',
                message=f'{request_obj.interviewer.get_full_name() or request_obj.interviewer.get_username()} '
                        f'proposed slots for {request_obj.get_round_type_display()} - pick one to confirm.',
                url=reverse('candidate_timeline', args=[request_obj.candidate_id]))
        slot_emails.notify_hr_slots_proposed(request_obj)
        messages.success(request, 'Slots submitted - HR will pick one and confirm the interview.')
        return redirect('interviewer_home')

    def _render(self, request, request_obj, form, status=200):
        return render(request, self.template_name, {
            'interview_request': request_obj, 'form': form,
            'back_url': reverse('interviewer_home'), 'back_label': 'My Interviews',
        }, status=status)
