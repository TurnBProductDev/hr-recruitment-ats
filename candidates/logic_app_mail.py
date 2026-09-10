"""Client for the `Send-Email-Notifier` Azure Logic App.

Every outbound email this app sends (interview invites, rejections,
interviewer notifications) goes through this one small Logic App instead of
SMTP - it reuses the Office 365 connection already authorized for
careers@turnb.com (the same one the CV-intake Logic Apps use), so no SMTP
password is ever needed. Same request/response shape and error-handling
style as candidates/cv_parser.py's LOGIC_APP_CV_PARSER_URL client.
"""
import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


class EmailSendError(Exception):
    """Raised when an email could not be sent. The message is shown to HR."""


def is_configured():
    return bool(getattr(settings, 'LOGIC_APP_EMAIL_SENDER_URL', ''))


def default_cc_list():
    """The two addresses every outbound email copies, per policy -
    careers@turnb.com (INTERVIEW_INVITE_FROM_EMAIL) and HR
    (INTERVIEW_INVITE_CC_EMAIL). Not user-editable. Shared by
    interviews/invites.py, interviews/slot_emails.py and
    candidates/rejection_emails.py so the policy lives in one place."""
    cc = []
    for addr in (
        getattr(settings, 'INTERVIEW_INVITE_FROM_EMAIL', '') or 'careers@turnb.com',
        getattr(settings, 'INTERVIEW_INVITE_CC_EMAIL', '') or 'Amrita.Sunilkumar@turnb.com',
    ):
        if addr and addr not in cc:
            cc.append(addr)
    return cc


def send_email(*, to_email, subject, body, cc_emails=None, attachments=None):
    """POST one email to the Logic App. `attachments` is an optional list of
    {'Name': str, 'ContentBytes': base64 str, 'ContentType': str}. Raises
    EmailSendError with a message fit for the HR user; never raises anything
    else."""
    if not is_configured():
        raise EmailSendError(
            'Email sending is not configured - set LOGIC_APP_EMAIL_SENDER_URL.')

    payload = {
        'to': to_email,
        'cc': '; '.join(cc_emails) if cc_emails else '',
        'subject': subject,
        'body': body,
    }
    if attachments:
        payload['attachments'] = attachments

    try:
        response = requests.post(
            settings.LOGIC_APP_EMAIL_SENDER_URL, json=payload, timeout=(15, 30))
    except requests.Timeout:
        raise EmailSendError('The email service did not respond in time.')
    except requests.RequestException as exc:
        raise EmailSendError(f'Could not reach the email service: {exc}')

    if response.status_code >= 400:
        raise EmailSendError(
            f'Email service returned HTTP {response.status_code}: {response.text[:300]}')
