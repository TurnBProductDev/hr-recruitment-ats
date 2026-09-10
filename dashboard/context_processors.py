from candidates.permissions import HR_ADMIN, RECRUITER, in_interviewer_portal, is_interviewer_only


def role_flags(request):
    """is_hr_admin/is_recruiter/is_interviewer_only/is_interviewer_portal on
    every template's context, so hr_base.html's nav (and every other
    template) can adapt without every view having to set them - some views
    already set is_hr_admin themselves for their own page-specific use (e.g.
    candidates/views.py's CandidateTimelineView); this just guarantees it's
    always there.

    is_hr_admin is deliberately a precise identity check (HR Admin/superuser
    only) - it is NOT redefined to include Recruiter, so it still means what
    its name says everywhere it's already used. Templates that should treat
    Recruiter the same way check `is_hr_admin or is_recruiter` explicitly
    instead (Recruiter now has the same create/modify/delete/Users-admin
    access as HR Admin throughout the main HR app).

    is_interviewer_only is identity ("is this account only ever an
    Interviewer") - still used for permission checks like CandidateCvView's.
    is_interviewer_portal is this session's current UI mode - true for that
    same identity, but also for an Admin whose session came in through the
    Interviewer login - and is what nav/breadcrumbs/back-links should key off."""
    user = getattr(request, 'user', None)
    is_hr_admin = bool(
        user and user.is_authenticated and (user.is_superuser or user.groups.filter(name=HR_ADMIN).exists()))
    is_recruiter = bool(user and user.is_authenticated and user.groups.filter(name=RECRUITER).exists())
    return {
        'is_hr_admin': is_hr_admin,
        'is_recruiter': is_recruiter,
        'is_interviewer_only': bool(user and is_interviewer_only(user)),
        'is_interviewer_portal': in_interviewer_portal(request),
    }
