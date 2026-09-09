from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied

HR_ADMIN = 'HR Admin'
RECRUITER = 'Recruiter'
INTERVIEWER = 'Interviewer'
HIRING_MANAGER = 'Hiring Manager'

ALL_GROUPS = (HR_ADMIN, RECRUITER, INTERVIEWER, HIRING_MANAGER)

# The main HR app (dashboard, candidate repository/lists, reports, job
# management, the full interview scheduler). Interviewer is deliberately not
# here - they get their own restricted portal instead (interviews/portal_views.py),
# confined to their scheduled interviews, the candidates on them, and
# recording results. A superuser always bypasses this via GroupRequiredMixin.
ANY_STAFF = (HR_ADMIN, RECRUITER, HIRING_MANAGER)


def is_interviewer_only(user):
    """True for an account whose only role is Interviewer - used to route it
    to the restricted portal instead of the main HR app (login redirect, nav
    visibility) rather than at every single view."""
    if not user.is_authenticated or user.is_superuser:
        return False
    groups = set(user.groups.values_list('name', flat=True))
    return INTERVIEWER in groups and not groups.intersection(ANY_STAFF)


# Session key InterviewerLoginView sets on a successful sign-in, and
# HRLoginView clears - "which door did you come in through", independent of
# is_interviewer_only(). An Admin's permissions never change (they can always
# reach the full HR app), but while they're in a session that started at the
# Interviewer login, the UI should behave like the portal throughout - nav,
# breadcrumbs, and a Record Result page's Cancel/back links all stay inside
# the portal instead of dropping them into the main HR app mid-flow.
PORTAL_SESSION_KEY = 'in_interviewer_portal'


def in_interviewer_portal(request):
    """True whenever this request should get the restricted Interviewer
    portal experience. Always true for an interviewer-only account (they
    have nowhere else to go); also true for anyone else (Admin) whose
    current session was started via the Interviewer login."""
    user = getattr(request, 'user', None)
    if user is not None and is_interviewer_only(user):
        return True
    session = getattr(request, 'session', None)
    return bool(session and session.get(PORTAL_SESSION_KEY))


class GroupRequiredMixin(LoginRequiredMixin):
    """Restricts a view to superusers or members of `allowed_groups`.

    Usage: class MyView(GroupRequiredMixin, ...):
               allowed_groups = (permissions.HR_ADMIN, permissions.RECRUITER)
    """
    allowed_groups = ANY_STAFF

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if not (request.user.is_superuser or request.user.groups.filter(name__in=self.allowed_groups).exists()):
            raise PermissionDenied("Your role does not have access to this page.")
        return super().dispatch(request, *args, **kwargs)
