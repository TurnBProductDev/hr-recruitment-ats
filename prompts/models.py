from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

# Placeholder tokens each prompt's text must still contain somewhere.
# Removing one doesn't crash anything (HR_management/template_text.render()
# never raises on an unmatched token) - it just silently stops that piece of
# context (the job's must-have requirements, HR's extra scoring criteria,
# how many questions to generate) from reaching the AI, which is easy to
# miss - so it's caught here instead, at save time.
REQUIRED_PLACEHOLDERS = {
    'match_scoring': ('{must_have_block}', '{extra_criteria_block}'),
    'screening_questions': ('{question_count}',),
}


class PromptTemplate(models.Model):
    """One admin-editable AI system prompt, looked up by `key` from
    prompts/store.py::render_prompt() - see that module's docstring for how
    an edit here reaches the actual Azure OpenAI call. Rows are seeded by
    migration (prompts/migrations/0002_seed_defaults.py) with today's exact
    wording; the admin (prompts/admin.py) only offers Change, not Add/Delete,
    since each row is tied to one specific call site in the code."""
    key = models.CharField(max_length=50, unique=True)
    label = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    text = models.TextField()
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        ordering = ['label']

    def __str__(self):
        return self.label

    def clean(self):
        missing = [p for p in REQUIRED_PLACEHOLDERS.get(self.key, ()) if p not in self.text]
        if missing:
            raise ValidationError(
                f'This prompt must still contain {", ".join(missing)} somewhere in the text - the '
                f'app fills these in automatically; removing them means that information no longer '
                f'reaches the AI.')
