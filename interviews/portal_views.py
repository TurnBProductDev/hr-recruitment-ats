"""The Interviewer portal: a small, read-mostly area confined to exactly what
an interviewer needs - the interviews assigned to them, the candidate on each
one, and recording a result (interviews.views.InterviewResultView, reused
as-is). Everything else in the app (dashboard, candidate repository, job
management, the full interview scheduler, ...) is off-limits to this role -
see candidates.permissions.ANY_STAFF, which deliberately excludes Interviewer.
"""
from django.contrib.auth import views as auth_views
from django.urls import reverse
from django.views.generic import DetailView, ListView

from candidates.models import Candidate
from candidates.permissions import INTERVIEWER, GroupRequiredMixin

from .models import Interview


class InterviewerLoginView(auth_views.LoginView):
    """A separate front door from the main HR sign-in (registration/login.html),
    for the link shared specifically with interviewers. Authenticates the
    same way, but refuses anyone who isn't actually in the Interviewer group
    (or a superuser) - they're pointed at the HR sign-in instead."""
    template_name = 'registration/interviewer_login.html'

    def get_success_url(self):
        return reverse('interviewer_home')

    def form_valid(self, form):
        user = form.get_user()
        if not (user.is_superuser or user.groups.filter(name=INTERVIEWER).exists()):
            form.add_error(None, 'This sign-in is for interviewers only. Use the HR sign-in instead.')
            return self.form_invalid(form)
        return super().form_valid(form)


class InterviewerHomeView(GroupRequiredMixin, ListView):
    """Landing page after an interviewer signs in: every interview assigned
    to them, split into what still needs a result and what's done."""
    model = Interview
    template_name = 'interviews/portal_home.html'
    context_object_name = 'interviews'
    allowed_groups = (INTERVIEWER,)

    def get_queryset(self):
        return (Interview.objects.filter(interviewer=self.request.user)
                .select_related('candidate', 'candidate__job').order_by('-scheduled_date'))

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        interviews = list(ctx['interviews'])
        ctx['pending'] = [i for i in interviews if i.status in Interview.OPEN_STATUSES]
        ctx['completed'] = [i for i in interviews if i.status not in Interview.OPEN_STATUSES]
        return ctx


class InterviewerCandidateView(GroupRequiredMixin, DetailView):
    """A read-only candidate view scoped to exactly what helps an interviewer
    prepare - contact/background/education/experience, the CV, the AI
    summary - with none of the HR-only actions (edit/delete, status changes,
    hiring-stage progression, vacancy/source mapping). Restricted to
    candidates this interviewer actually has an interview with, not the whole
    repository by ID - a 404, not just a group check, for anyone else."""
    model = Candidate
    template_name = 'interviews/portal_candidate.html'
    context_object_name = 'candidate'
    allowed_groups = (INTERVIEWER,)

    def get_queryset(self):
        return Candidate.objects.filter(interviews__interviewer=self.request.user).distinct()

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['my_interviews'] = (self.object.interviews.filter(interviewer=self.request.user)
                                .order_by('-scheduled_date'))
        ctx['open_statuses'] = Interview.OPEN_STATUSES
        ctx['back_url'] = reverse('interviewer_home')
        ctx['back_label'] = 'My Interviews'
        return ctx
