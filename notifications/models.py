from django.conf import settings
from django.db import models


class Notification(models.Model):
    """A generic in-app notification for one user. Created alongside an email
    wherever this app needs to tell someone "go do something" - see
    notifications.services.notify(), which is the only way these should be
    created, and interviews/slot_emails.py for the paired email side."""
    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='notifications')
    title = models.CharField(max_length=200)
    message = models.TextField(blank=True)
    url = models.CharField(max_length=500, blank=True)
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.recipient}: {self.title}'
