from candidates.permissions import HR_ADMIN


def role_flags(request):
    """is_hr_admin on every template's context, so hr_base.html's nav can show
    the Users link only to admins without every view having to set it - some
    views already set it themselves for their own page-specific use (e.g.
    candidates/views.py's CandidateTimelineView); this just guarantees it's
    always there."""
    user = getattr(request, 'user', None)
    is_hr_admin = bool(
        user and user.is_authenticated and (user.is_superuser or user.groups.filter(name=HR_ADMIN).exists()))
    return {'is_hr_admin': is_hr_admin}
