from django.contrib import messages
from django.contrib.auth import login as auth_login, views as auth_views
from django.contrib.auth.models import User
from django.shortcuts import redirect
from django.urls import reverse
from django.views import View

from candidates.permissions import HR_ADMIN, INTERVIEWER, PORTAL_SESSION_KEY, in_interviewer_portal, is_interviewer_only

from . import azure_auth
from .password_forms import BootstrapPasswordChangeForm


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

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['azure_login_available'] = azure_auth.is_configured()
        return ctx

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


class HRPasswordChangeView(auth_views.PasswordChangeView):
    """Self-service "Change Password" while logged in - reachable by HR and
    Interviewer accounts alike (linked from the shared account dropdown in
    templates/hr_base.html). A messages.success() + redirect back to
    wherever this role actually lands, rather than Django's separate "done"
    page, to match this app's messages-based feedback used everywhere else."""
    template_name = 'registration/password_change_form.html'
    form_class = BootstrapPasswordChangeForm

    def get_success_url(self):
        # in_interviewer_portal (session-mode-aware, same as
        # InterviewResultView.get_success_url), not just is_interviewer_only -
        # an Admin who signed in via the Interviewer login stays in the
        # portal experience, same as everywhere else portal mode matters.
        if in_interviewer_portal(self.request) or is_interviewer_only(self.request.user):
            return reverse('interviewer_home')
        return reverse('hr_dashboard')

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, 'Password changed.')
        return response


class AzureLoginView(View):
    """Starts "Sign in with Microsoft" from either door - the HR login
    (registration/login.html) or the Interviewer login
    (registration/interviewer_login.html), told apart by ?portal=interviewer
    on the link each template renders. Both doors share the one redirect URI
    registered on the Azure AD app (AzureCallbackView), since that's fixed
    per app registration."""

    def get(self, request):
        portal = request.GET.get('portal') == 'interviewer'
        login_url_name = 'interviewer_login' if portal else 'login'
        if not azure_auth.is_configured():
            return redirect(login_url_name)
        request.session['azure_login_portal'] = portal
        redirect_uri = request.build_absolute_uri(reverse('azure_callback'))
        auth_url = azure_auth.build_auth_url(request, redirect_uri, request.GET.get('next', ''))
        return redirect(auth_url)


class AzureCallbackView(View):
    """Where Azure AD sends the browser back to after Microsoft sign-in.
    Verifies identity only (see HR_management/azure_auth.py) - what the
    matched Django account can then do is entirely down to its existing
    Group membership, exactly as for a password sign-in. An email with no
    matching active User (or more than one - a data-integrity edge case, not
    silently picked between) is turned away: this never creates accounts."""

    def get(self, request):
        portal = request.session.pop('azure_login_portal', False)
        login_url_name = 'interviewer_login' if portal else 'login'
        if not azure_auth.is_configured():
            return redirect(login_url_name)

        error_description = request.GET.get('error_description') or request.GET.get('error')
        if error_description:
            messages.error(request, 'Microsoft sign-in was cancelled.')
            return redirect(login_url_name)

        redirect_uri = request.build_absolute_uri(reverse('azure_callback'))
        try:
            email, next_url = azure_auth.acquire_user_email(
                request, code=request.GET.get('code', ''), state=request.GET.get('state', ''),
                redirect_uri=redirect_uri)
        except azure_auth.AzureAuthError as exc:
            messages.error(request, str(exc))
            return redirect(login_url_name)

        matches = User.objects.filter(email__iexact=email, is_active=True)
        if matches.count() != 1:
            messages.error(
                request,
                f'No HireB account found for {email}. Ask an admin to add you as a User first.')
            return redirect(login_url_name)
        user = matches.first()

        if portal and not (user.is_superuser or user.groups.filter(name__in=(HR_ADMIN, INTERVIEWER)).exists()):
            messages.error(request, 'This sign-in is for interviewers only. Use the HR sign-in instead.')
            return redirect('interviewer_login')

        auth_login(request, user, backend='django.contrib.auth.backends.ModelBackend')
        request.session[PORTAL_SESSION_KEY] = bool(portal)

        if next_url:
            return redirect(next_url)
        if portal or is_interviewer_only(user):
            return redirect('interviewer_home')
        return redirect('hr_dashboard')
