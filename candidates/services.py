"""Duplicate/blacklist detection and status-transition helpers for the
candidates app, built around the email_registry + blacklist tables.
"""
import uuid

from .models import (
    PLACEHOLDER_EMAIL_DOMAIN,
    Blacklist,
    Candidate,
    CandidateEducation,
    CandidateStatusHistory,
    EmailRegistry,
)

STATUS = Candidate.Status


def placeholder_email():
    """Stand-in address for a candidate whose CV had no readable email."""
    return f"pending-{uuid.uuid4().hex[:12]}@{PLACEHOLDER_EMAIL_DOMAIN}"


def check_email(email):
    """Look up an applicant's email before creating a Candidate row.

    Returns (is_duplicate, is_blacklisted):
      - blacklisted: this email has an active Blacklist entry -> new
        application is auto-blacklisted too.
      - duplicate: email already exists in the registry (a "Reapply") but
        is not blacklisted.
      - neither: brand new applicant.
    """
    registry_entry = EmailRegistry.objects.filter(email__iexact=email).first()
    if registry_entry is None:
        return False, False

    is_blacklisted = Blacklist.objects.filter(candidate__email__iexact=email).exists()
    return True, is_blacklisted


def register_application(candidate):
    """Create/update the email_registry row for this candidate's email."""
    entry = EmailRegistry.objects.filter(email__iexact=candidate.email).first()
    if entry is None:
        return EmailRegistry.objects.create(
            email=candidate.email, first_candidate=candidate, application_count=1
        )
    entry.application_count = entry.application_count + 1
    entry.save(update_fields=['application_count', 'last_applied_at'])
    return entry


def record_creation(candidate, user=None, remarks=None, performed_by=None):
    CandidateStatusHistory.objects.create(
        candidate=candidate, old_status='', new_status=candidate.status,
        changed_by=user, performed_by=performed_by, remarks=remarks,
    )


def change_status(candidate, new_status, user=None, remarks=None, performed_by=None):
    old_status = candidate.status
    if old_status == new_status:
        return candidate
    candidate.status = new_status
    # Hold is available from every stage, so remember which one it was taken
    # from - that is what names it ("Interview Hold"). Leaving hold clears it.
    candidate.hold_from_status = old_status if new_status == STATUS.SCREENING_HOLD else ''
    candidate.save(update_fields=['status', 'hold_from_status', 'updated_at'])
    CandidateStatusHistory.objects.create(
        candidate=candidate, old_status=old_status, new_status=new_status,
        changed_by=user, performed_by=performed_by, remarks=remarks,
    )
    return candidate


def hold_source_from_history(candidate):
    """Which stage the candidate's current hold was taken from, read back from
    history. Used when a status is restored rather than changed (undo)."""
    if candidate.status != STATUS.SCREENING_HOLD:
        return ''
    last_hold = (candidate.history.filter(new_status=STATUS.SCREENING_HOLD)
                 .order_by('-changed_at', '-id').first())
    return last_hold.old_status if last_hold else ''


def blacklist_candidate(candidate, reason, user=None, performed_by=None):
    Blacklist.objects.create(candidate=candidate, reason=reason, blacklisted_by=user)
    candidate.is_blacklisted = True
    candidate.save(update_fields=['is_blacklisted'])
    change_status(candidate, STATUS.BLACKLISTED, user=user, remarks=reason, performed_by=performed_by)
    return candidate


def create_from_parsed_cv(fields, job, source, user=None, performed_by=None, remarks=None):
    """Create a Candidate from Logic-App-parsed CV fields (Bulk Upload CV).

    Runs the same duplicate/blacklist checks as the careers form, and builds the
    structured education record from the "Degree - College - Year" string the
    same way sql/sp_intake_add_candidate.sql does. The vacancy and source come
    from the upload screen, not from the CV.
    """
    from . import cv_parser  # local import: cv_parser imports settings/requests

    email = (fields.get('email') or '').strip().lower()
    has_real_email = bool(email)
    is_duplicate, is_blacklisted = check_email(email) if has_real_email else (False, False)

    candidate = Candidate(
        full_name=fields.get('full_name') or 'Unnamed Candidate',
        email=email or placeholder_email(),
        phone=fields.get('phone'),
        qualification=fields.get('qualification'),
        cv_summary=fields.get('cv_summary'),
        resume_url=fields.get('resume_url'),
        role_applied=fields.get('role_applied') or (job.title if job else None),
        job=job,
        source=source,
        is_duplicate=is_duplicate,
        is_blacklisted=is_blacklisted,
        status=STATUS.BLACKLISTED if is_blacklisted else STATUS.OPEN,
    )
    candidate.save()

    if has_real_email:
        register_application(candidate)

    qualification, institution, year = cv_parser.split_education(fields.get('qualification'))
    if qualification:
        CandidateEducation.objects.create(
            candidate=candidate, qualification=qualification,
            institution=institution, year_completed=year,
        )

    record_creation(candidate, user=user, remarks=remarks, performed_by=performed_by)
    return candidate


def submit_application(candidate):
    """Run duplicate/blacklist detection, save the candidate, log the
    initial status, and register the email. Called from the public
    application form on submit.
    """
    is_duplicate, is_blacklisted = check_email(candidate.email)
    candidate.is_duplicate = is_duplicate
    candidate.is_blacklisted = is_blacklisted
    candidate.status = STATUS.BLACKLISTED if is_blacklisted else STATUS.OPEN
    candidate.save()
    register_application(candidate)
    record_creation(candidate)
    return candidate
