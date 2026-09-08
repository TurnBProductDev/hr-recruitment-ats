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
