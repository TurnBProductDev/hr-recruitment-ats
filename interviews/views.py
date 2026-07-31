from django.contrib import messages
from django.core.mail import send_mail
from django.db.models import BooleanField, Case, Value, When
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.generic import CreateView, ListView, UpdateView

from candidates import services
from candidates.models import Candidate
from candidates.permissions import ANY_STAFF, HR_ADMIN, INTERVIEWER, RECRUITER, GroupRequiredMixin

from .forms import InterviewForm, InterviewResultForm
from .models import Interview


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
                status__in=[Interview.Status.SCHEDULED, Interview.Status.RESCHEDULED],
                result=Interview.Result.PENDING)
              .select_related('candidate', 'candidate__job', 'interviewer')
              .annotate(is_upcoming=Case(
                  When(scheduled_date__gt=timezone.now(), then=Value(True)),
                  default=Value(False), output_field=BooleanField())))
        q = self.request.GET.get('q')
        if q:
            qs = qs.filter(candidate__full_name__icontains=q)
        return qs.order_by('scheduled_date')


class InterviewScheduleView(GroupRequiredMixin, CreateView):
    model = Interview
    form_class = InterviewForm
    template_name = 'interviews/interview_form.html'
    allowed_groups = (HR_ADMIN, RECRUITER)

    def dispatch(self, request, *args, **kwargs):
        self.candidate = get_object_or_404(Candidate, pk=kwargs['candidate_id'])
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['candidate'] = self.candidate
        return ctx

    def form_valid(self, form):
        form.instance.candidate = self.candidate
        form.instance.created_by = self.request.user
        messages.success(self.request, f'Interview scheduled for {self.candidate.full_name}.')
        return super().form_valid(form)

    def get_success_url(self):
        return reverse('candidate_timeline', args=[self.candidate.pk])


class InterviewRescheduleView(GroupRequiredMixin, UpdateView):
    model = Interview
    form_class = InterviewForm
    template_name = 'interviews/interview_form.html'
    allowed_groups = (HR_ADMIN, RECRUITER)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['candidate'] = self.object.candidate
        return ctx

    def form_valid(self, form):
        form.instance.status = Interview.Status.RESCHEDULED
        messages.success(self.request, 'Interview rescheduled.')
        return super().form_valid(form)

    def get_success_url(self):
        return reverse('candidate_timeline', args=[self.object.candidate_id])


class InterviewResultView(GroupRequiredMixin, UpdateView):
    model = Interview
    form_class = InterviewResultForm
    template_name = 'interviews/interview_result_form.html'
    allowed_groups = (HR_ADMIN, INTERVIEWER)

    # On Pass, advance the candidate one stage down the pipeline rather than
    # hiring outright: Round 1 -> Interview (Round 2) -> Final Selection, where HR
    # makes the actual hire decision. Anything not listed falls back to Hired.
    PASS_NEXT = {
        Candidate.Status.SHORTLISTED: Candidate.Status.ROUND1,
        Candidate.Status.ROUND1: Candidate.Status.INTERVIEW,
        Candidate.Status.INTERVIEW: Candidate.Status.FINAL_SELECTION,
        Candidate.Status.FINAL_SELECTION: Candidate.Status.HIRED,
    }

    def form_valid(self, form):
        # Marking a result completes the interview and drives the candidate's
        # pipeline: Pass -> next stage, Fail -> Rejected.
        form.instance.status = Interview.Status.COMPLETED
        response = super().form_valid(form)
        interview = self.object
        candidate = interview.candidate
        performed_by = self.request.user.get_full_name() or self.request.user.get_username()
        round_label = interview.get_round_type_display()
        if interview.result == Interview.Result.PASS_:
            next_status = self.PASS_NEXT.get(candidate.status, Candidate.Status.HIRED)
            services.change_status(candidate, next_status, user=self.request.user,
                                   remarks=f'{round_label} interview passed.', performed_by=performed_by)
            messages.success(self.request, f'{candidate.full_name} passed — moved to "{candidate.get_status_display()}".')
        elif interview.result == Interview.Result.FAIL:
            services.change_status(candidate, Candidate.Status.REJECTED, user=self.request.user,
                                   remarks=f'{round_label} interview failed.', performed_by=performed_by)
            messages.success(self.request, f'{candidate.full_name} failed and was moved to Rejected.')
        else:
            messages.success(self.request, 'Interview result recorded.')
        return response

    def get_success_url(self):
        return reverse('interview_scheduler')


class InterviewSendInviteView(GroupRequiredMixin, View):
    """Sends the invite via Django's configured email backend (console
    backend in dev - see settings.EMAIL_BACKEND). Wire up real SMTP
    settings in production to actually deliver these."""
    allowed_groups = (HR_ADMIN, RECRUITER)

    def post(self, request, pk):
        interview = get_object_or_404(Interview, pk=pk)
        candidate = interview.candidate
        if candidate.email:
            send_mail(
                subject=f'Interview Invitation - {interview.get_round_type_display()}',
                message=(
                    f"Dear {candidate.full_name},\n\n"
                    f"You are invited for a {interview.get_round_type_display()} interview on "
                    f"{interview.scheduled_date:%d %b %Y %H:%M} ({interview.get_mode_display()}).\n"
                    f"{'Meeting link: ' + interview.meeting_link if interview.meeting_link else ''}\n\n"
                    f"Regards,\nHR Team"
                ),
                from_email=None,
                recipient_list=[candidate.email],
                fail_silently=True,
            )
        messages.success(request, f'Invite sent to {candidate.email}.')
        return redirect('candidate_timeline', pk=candidate.pk)
