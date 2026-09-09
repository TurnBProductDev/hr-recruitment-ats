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
from candidates.permissions import HR_ADMIN, INTERVIEWER, PORTAL_SESSION_KEY, GroupRequiredMixin

from .models import Interview


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
    """Landing page after an interviewer signs in: every interview assigned
    to them, split into what still needs a result and what's done. An Admin
    sees every interview, for every interviewer, instead of just their own."""
    model = Interview
    template_name = 'interviews/portal_home.html'
    context_object_name = 'interviews'
    allowed_groups = (INTERVIEWER, HR_ADMIN)

    def get_queryset(self):
        qs = Interview.objects.select_related('candidate', 'candidate__job', 'interviewer')
        if not _is_admin(self.request.user):
            qs = qs.filter(interviewer=self.request.user)
        return qs.order_by('-scheduled_date')

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['is_admin_view'] = _is_admin(self.request.user)
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
