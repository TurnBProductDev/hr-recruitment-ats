from django.contrib.auth import views as auth_views
from django.urls import reverse

from candidates.permissions import is_interviewer_only


class HRLoginView(auth_views.LoginView):
    """The main sign-in page (registration/login.html). An Interviewer-only
    account is welcome to use it too (nothing here blocks that, unlike
    interviews.portal_views.InterviewerLoginView blocking the reverse case) -
    it just lands them on their own portal instead of the hr_dashboard they
    have no access to (see candidates/permissions.py's ANY_STAFF)."""

    def get_success_url(self):
        if is_interviewer_only(self.request.user):
            return reverse('interviewer_home')
        return super().get_success_url()


class HRLogoutView(auth_views.LogoutView):
    """Logging out used to land on the public careers page
    (LOGOUT_REDIRECT_URL='vacancy_list'), which reads like something went
    wrong rather than a normal sign-out. Sends everyone back to a sign-in
    page instead - an Interviewer to their own (interviewer_login), so the
    door they came in through is the one they land back on; everyone else to
    the main HR sign-in."""

    def post(self, request, *args, **kwargs):
        # auth_logout() (called by the parent's post()) clears request.user
        # to AnonymousUser before get_default_redirect_url() runs - the role
        # has to be captured here, before that happens.
        self._was_interviewer_only = is_interviewer_only(request.user)
        return super().post(request, *args, **kwargs)

    def get_default_redirect_url(self):
        if self._was_interviewer_only:
            return reverse('interviewer_login')
        return reverse('login')
