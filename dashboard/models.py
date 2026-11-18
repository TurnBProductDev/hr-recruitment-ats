from django.conf import settings
from django.db import models


class AccountChangeLog(models.Model):
    """One row per change to a login account - role, name, email, login ID,
    active/inactive, password set/changed, created or deleted - with who made
    it and when. Written by dashboard/user_audit.py whichever way the change
    was made (the Users page, Django admin, self-service password change, a
    password-reset link). Never holds a password, only that it changed."""
    account = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                related_name='change_logs')
    # Kept as text too, so the history still reads correctly if either
    # account is later deleted.
    account_name = models.CharField(max_length=255)
    changed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='+')
    changed_by_name = models.CharField(max_length=255)
    summary = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at', '-id']

    @property
    def summary_parts(self):
        return self.summary.split('; ')

    def __str__(self):
        return f'{self.created_at:%Y-%m-%d %H:%M} {self.changed_by_name}: {self.account_name} - {self.summary}'
