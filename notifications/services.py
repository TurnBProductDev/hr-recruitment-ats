from .models import Notification


def notify(recipient, title, message='', url=''):
    """Create one in-app notification. Call this next to whatever email is
    being sent for the same event - the two are meant to always fire
    together (email + in-app), never one without the other."""
    if recipient is None:
        return None
    return Notification.objects.create(recipient=recipient, title=title, message=message, url=url)
