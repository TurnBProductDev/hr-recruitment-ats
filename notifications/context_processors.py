def unread_notifications(request):
    """unread_notification_count + the most recent unread rows on every
    template's context, so hr_base.html's bell icon can render without every
    view having to set it - same pattern as dashboard.context_processors.role_flags."""
    user = getattr(request, 'user', None)
    if not (user and user.is_authenticated):
        return {'unread_notification_count': 0, 'recent_notifications': []}
    qs = user.notifications.filter(is_read=False)
    return {
        'unread_notification_count': qs.count(),
        'recent_notifications': qs[:8],
    }
