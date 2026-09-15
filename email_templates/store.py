"""Looks up an admin-edited email subject/body (email_templates/models.py::
EmailTemplate, editable at /admin/) by key, falling back to the hardcoded
defaults passed in if no row exists yet (e.g. a fresh environment before
migrations run). Not cached, same reasoning as prompts/store.py - an edit
takes effect on the very next email sent, no restart needed.
"""
from HR_management.template_text import render

from .models import EmailTemplate


def render_email(key, default_subject, default_body, **context):
    try:
        row = EmailTemplate.objects.get(key=key)
        subject_template, body_template = row.subject or default_subject, row.body or default_body
    except EmailTemplate.DoesNotExist:
        subject_template, body_template = default_subject, default_body
    return render(subject_template, **context), render(body_template, **context)
