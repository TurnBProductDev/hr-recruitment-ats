"""Looks up an admin-edited prompt (prompts/models.py::PromptTemplate,
editable at /admin/) by key, falling back to the hardcoded default each
prompts/<feature>.py module still ships (used if no row exists yet, e.g. a
fresh environment before migrations run). Not cached - a scoring/extraction
call to Azure OpenAI already takes seconds, one extra SELECT is negligible
next to that, and it means an admin's edit takes effect on the very next
call, no restart needed.
"""
from HR_management.template_text import render

from .models import PromptTemplate


def render_prompt(key, default, **context):
    try:
        text = PromptTemplate.objects.get(key=key).text or default
    except PromptTemplate.DoesNotExist:
        text = default
    return render(text, **context)
