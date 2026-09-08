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
