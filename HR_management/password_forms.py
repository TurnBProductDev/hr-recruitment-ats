"""Password-reset email, sent through the same Send-Email-Notifier Logic App
every other outbound email in this app uses (candidates/logic_app_mail.py) -
not Django's own EMAIL_BACKEND, which isn't wired to anything real here (see
HR_management/settings.py's EMAIL_BACKEND comment). Everything else (the
uid/token, the one-time-use link) is Django's own PasswordResetForm/
PasswordResetConfirmView machinery, untouched.
"""
import logging

from django.contrib.auth.forms import PasswordChangeForm, PasswordResetForm, SetPasswordForm
from django.contrib.auth.tokens import default_token_generator
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from candidates import logic_app_mail
from dashboard.user_forms import BootstrapFormMixin
from email_templates.store import render_email

logger = logging.getLogger(__name__)

# Fallback defaults, used only if the 'password_reset' EmailTemplate row is
# missing (e.g. before migrations run) - the seeded row
# (email_templates/migrations/0002_seed_defaults.py) carries this same text,
# and is what an admin actually edits at /admin/.
_DEFAULT_SUBJECT = 'Reset your TurnB ATS password'
_DEFAULT_BODY = (
    'Hello {name},\n\n'
    'We received a request to reset your TurnB ATS password. Click the link below to '
    'choose a new one:\n\n'
    '{reset_url}\n\n'
    "If you didn't request this, you can safely ignore this email - your password "
    "won't be changed.\n\n"
    'Regards,\n'
    'TurnB ATS'
)


class BootstrapPasswordChangeForm(BootstrapFormMixin, PasswordChangeForm):
    """PasswordChangeForm styled to match every other form in this app (see
    dashboard/user_forms.py's BootstrapFormMixin)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._add_bootstrap_classes()


class BootstrapSetPasswordForm(BootstrapFormMixin, SetPasswordForm):
    """SetPasswordForm (the "pick a new password" half of the reset link
    flow), styled the same way."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._add_bootstrap_classes()


class LogicAppPasswordResetForm(BootstrapFormMixin, PasswordResetForm):
    """Same lookup/validation as Django's PasswordResetForm - only how the
    email is actually sent differs. Overriding save() (rather than the usual
    send_mail() hook) skips Django's email-template rendering entirely, to
    match this codebase's convention of building email bodies as plain
    f-strings (see interviews/slot_emails.py, interviews/invites.py)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._add_bootstrap_classes()

    def save(self, request=None, **kwargs):
        for user in self.get_users(self.cleaned_data['email']):
            uid = urlsafe_base64_encode(force_bytes(user.pk))
            token = default_token_generator.make_token(user)
            reset_path = reverse('password_reset_confirm', kwargs={'uidb64': uid, 'token': token})
            reset_url = request.build_absolute_uri(reset_path) if request else reset_path
            subject, body = render_email(
                'password_reset', _DEFAULT_SUBJECT, _DEFAULT_BODY,
                name=user.get_full_name() or user.get_username(), reset_url=reset_url)
            try:
                logic_app_mail.send_email(to_email=user.email, subject=subject, body=body)
            except logic_app_mail.EmailSendError as exc:
                # Best-effort, same as every other notification email in this
                # app - and never surfaced to the requester, who shouldn't be
                # able to tell whether the address they typed exists at all.
                logger.warning('Could not send password reset email to %s: %s', user.email, exc)
