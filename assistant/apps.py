from django.apps import AppConfig


class AssistantConfig(AppConfig):
    """"Ask the ATS" - the floating HR assistant (read-only AI chat over the
    ATS data and user guide)."""
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'assistant'
