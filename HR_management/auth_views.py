from django.contrib.auth import views as auth_views
from django.urls import reverse

from candidates.permissions import PORTAL_SESSION_KEY, in_interviewer_portal, is_interviewer_only


class HRLoginView(auth_views.LoginView):
    """The main sign-in page (registration/login.html). An Interviewer-only
    account is welcome to use it too (nothing here blocks that, unlike
    interviews.portal_views.InterviewerLoginView blocking the reverse case) -
    it just lands them on their own portal instead of the hr_dashboard they
    have no access to (see candidates/permissions.py's ANY_STAFF)."""

    def form_valid(self, form):
        response = super().form_valid(form)
        # Coming in this door always means the full HR experience, even for
        # an Admin whose previous session (in the same browser) had been
        # marked portal-mode by signing in via the Interviewer login instead.
        self.request.session[PORTAL_SESSION_KEY] = False
        return response

    def get_success_url(self):
        if is_interviewer_only(self.request.user):
            return reverse('interviewer_home')
        return super().get_success_url()


class HRLogoutView(auth_views.LogoutView):
    """Logging out used to land on the public careers page
    (LOGOUT_REDIRECT_URL='vacancy_list'), which reads like something went
    wrong rather than a normal sign-out. Sends everyone back to a sign-in
    page instead - whichever door this session came in through (see
    candidates.permissions.in_interviewer_portal), so a plain Interviewer or
    an Admin who signed in via the Interviewer login both land back on
    interviewer_login; everyone else lands on the main HR sign-in."""

    def post(self, request, *args, **kwargs):
        # auth_logout() (called by the parent's post()) clears the session
        # and request.user before get_default_redirect_url() runs - this has
        # to be captured here, before that happens.
        self._was_portal_session = in_interviewer_portal(request)
        return super().post(request, *args, **kwargs)

    def get_default_redirect_url(self):
        if self._was_portal_session:
            return reverse('interviewer_login')
        return reverse('login')
