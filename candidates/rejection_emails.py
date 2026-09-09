"""Builds the "thank you for interviewing, but not this time" rejection
email sent from the Round 1, Round 2 and Final Decision stages' Reject
button - not CV Screening/Tele Screening, where the candidate was never
actually interviewed and this "thank you for your time in the interview
process" wording wouldn't fit (see candidates/views.py's
CandidateRejectionDraftView / CandidateSendRejectionView, and the "Reject"
button's data-load-url only being set for those three stages in
timeline.html).

Same shape as interviews/invites.py's interview invite: a starting draft the
HR user reviews and can edit before sending, with the recipient/CC always
derived server-side rather than trusted from the client.
"""
from django.conf import settings
from django.core.mail import EmailMessage


def default_subject(candidate):
    return 'Update on your application to TurnB Business Services'


def default_body(candidate):
    return (
        f'Hello {candidate.full_name},\n\n'
        'I hope this e-mail finds you well. I wanted to reach out to you personally and thank you '
        'for all the time and energy that you have invested for the interview process at TurnB '
        'Business Services Pvt. Ltd.  We have thoroughly enjoyed getting to know you throughout the '
        'process.\n\n'
        'Our performance bars were high but unfortunately you could not meet those this time. We '
        'have planned to move forward with other candidates this time whose skills closely align '
        'with the specific needs of the position.\n\n'
        'We wish you every success in your future endeavors!\n\n'
        'Regards,\n'
        'HRBP\n'
        'TurnB Business Services Pvt Ltd'
    )


def default_cc_list():
    """Fixed HR addresses, not user-editable - same policy and same settings
    as interviews/invites.py's interview invite CC."""
    cc = []
    from_email = getattr(settings, 'INTERVIEW_INVITE_FROM_EMAIL', '') or 'careers@turnb.com'
    fixed = getattr(settings, 'INTERVIEW_INVITE_CC_EMAIL', '') or 'Amrita.Sunilkumar@turnb.com'
    for addr in (from_email, fixed):
        if addr and addr not in cc:
            cc.append(addr)
    return cc


def send_rejection_email(*, to_email, cc_emails, subject, body):
    """Send the rejection email. Raises on failure - the caller decides how
    to surface that (never silently swallowed)."""
    from_email = getattr(settings, 'INTERVIEW_INVITE_FROM_EMAIL', None) or settings.DEFAULT_FROM_EMAIL
    email = EmailMessage(subject=subject, body=body, from_email=from_email, to=[to_email], cc=list(cc_emails))
    email.send(fail_silently=False)
