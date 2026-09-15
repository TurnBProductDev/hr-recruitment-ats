from django.conf import settings
from django.db import models


class EmailTemplate(models.Model):
    """One admin-editable outbound email (subject + body), looked up by
    `key` from email_templates/store.py::render_email() - see that module's
    docstring for how an edit here reaches the actual outbound email. Rows
    are seeded by migration (email_templates/migrations/0002_seed_defaults.py)
    with today's exact wording; the admin (email_templates/admin.py) only
    offers Change, not Add/Delete, since each row is tied to one specific
    call site in the code."""
    key = models.CharField(max_length=50, unique=True)
    label = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    subject = models.CharField(max_length=255)
    body = models.TextField()
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        ordering = ['label']

    def __str__(self):
        return self.label
