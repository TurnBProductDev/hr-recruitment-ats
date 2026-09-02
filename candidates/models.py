import json
import os
import uuid

from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils import timezone

from jobs.models import Job


# Canonical recruitment-source names. Every writer (careers form, bulk upload,
# the Logic App intake proc, the legacy importer) must use these exact strings —
# a variant spelling silently splits one source into two on the dashboard.
CAREERS = 'Careers'
CANONICAL_SOURCES = [CAREERS, 'Linked In', 'Referral', 'Naukri', 'Agency', 'Other']

# Known messy spellings -> canonical. Keyed by the value lowercased with spaces
# removed, so 'LinkedIn', 'linked in', 'LINKEDIN' all collapse to 'Linked In'.
# The SQL mirror of this lives in sql/sp_intake_add_candidate.sql — keep them in sync.
_SOURCE_ALIASES = {
    'linkedin': 'Linked In',
    'careers': CAREERS,
    'careersportal': CAREERS,
    'referral': 'Referral',
    'employeereference': 'Referral',
    'naukri': 'Naukri',
    'agency': 'Agency',
}


# Stand-in address for a candidate whose CV had no readable email (bulk upload).
# Never entered into the email registry, so it can't count as a duplicate.
PLACEHOLDER_EMAIL_DOMAIN = 'placeholder.local'


def canonical_source(value):
    """Map a raw source string onto its canonical name (leaves unknown ones as-is,
    only trimmed). Applied on every save so the dashboard never splits a source."""
    if not value:
        return value
    s = value.strip()
    return _SOURCE_ALIASES.get(s.lower().replace(' ', ''), s)


def generate_candidate_code():
    """Candidate code: ID + current year + 5-digit sequence, resetting per year.
    e.g. ID202600001. Sequence continues from the highest code for this year."""
    prefix = f"ID{timezone.now():%Y}"
    last = (Candidate.objects.filter(candidate_code__startswith=prefix)
            .order_by('-candidate_code').values_list('candidate_code', flat=True).first())
    seq = 1
    if last:
        tail = last[len(prefix):]
        if tail.isdigit():
            seq = int(tail) + 1
    return f"{prefix}{seq:05d}"


def candidate_resume_path(instance, filename):
    """cvs/<job_id>/<candidate_code>/resume.<ext> - matches the Blob Storage
    layout the design calls for; swapping DEFAULT_FILE_STORAGE to Azure Blob
    later needs no change here."""
    ext = filename.rsplit('.', 1)[-1] if '.' in filename else 'pdf'
    job_part = instance.job_id or 'general'
    return f"cvs/{job_part}/{instance.candidate_code}/resume.{ext}"


class Candidate(models.Model):
    class MatchState(models.TextChoices):
        PENDING = 'PENDING', 'Not scored'
        SCORING = 'SCORING', 'Scoring'
        DONE = 'DONE', 'Scored'
        ERROR = 'ERROR', 'Failed'

    class Status(models.TextChoices):
        OPEN = 'OPEN', 'Open'
        SHORTLISTED = 'SHORTLISTED', 'Shortlisted'
        ROUND1 = 'ROUND1', 'Round 1'
        INTERVIEW = 'INTERVIEW', 'Interview'
        FINAL_SELECTION = 'FINAL_SELECTION', 'Final Selection'
        HIRED = 'HIRED', 'Hired'
        REJECTED = 'REJECTED', 'Rejected'
        BLACKLISTED = 'BLACKLISTED', 'Blacklisted'
        # Hold can be applied from any stage, not just screening. The stored
        # value stays SCREENING_HOLD so existing rows, links and the badge
        # colour keep working; what the user sees is the stage they were held
        # at - "Interview Hold", "Round 1 Hold" - see hold_label().
        SCREENING_HOLD = 'SCREENING_HOLD', 'Hold'

    candidate_code = models.CharField(max_length=20, unique=True, blank=True)
    job = models.ForeignKey(Job, on_delete=models.SET_NULL, null=True, blank=True, related_name='candidates')

    full_name = models.CharField(max_length=255)
    email = models.EmailField()
    phone = models.CharField(max_length=20, blank=True, null=True)
    dob = models.DateField(blank=True, null=True)
    current_location = models.CharField(max_length=255, blank=True, null=True)

    qualification = models.CharField('Last Education', max_length=255, blank=True, null=True)
    institution = models.CharField('Last Institution', max_length=255, blank=True, null=True)
    last_role = models.CharField(max_length=255, blank=True, null=True)
    last_company = models.CharField(max_length=255, blank=True, null=True)
    total_experience_years = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True)
    skills = models.TextField(blank=True, null=True)

    linkedin = models.URLField(blank=True, null=True)
    portfolio_url = models.URLField(blank=True, null=True)
    notice_period = models.CharField(max_length=100, blank=True, null=True)
    expected_salary = models.CharField(max_length=100, blank=True, null=True)
    current_salary = models.CharField(max_length=100, blank=True, null=True)

    status = models.CharField(max_length=30, choices=Status.choices, default=Status.OPEN)
    # Which stage the candidate was at when put on Hold, so the hold can be
    # named after it. Blank for anyone not on hold. Kept on the row (the same
    # thing is derivable from history) so candidate lists need no extra query.
    hold_from_status = models.CharField(max_length=30, blank=True, default='')
    source = models.CharField(max_length=255, blank=True, null=True)
    # The position the applicant actually asked for, as written on the CV / in the
    # application email. `job` is where we filed them, which is often
    # "General Application" when the text matched no open vacancy - this keeps the
    # original wording so those applicants can still be sorted by what they wanted.
    role_applied = models.CharField('Applied Position', max_length=255, blank=True, null=True)
    resume_blob_url = models.FileField('Resume', upload_to=candidate_resume_path, blank=True, null=True)
    resume_url = models.URLField('Resume Link', max_length=1000, blank=True, null=True,
                                 help_text='External link to the resume (e.g. SharePoint/Drive).')

    is_duplicate = models.BooleanField(default=False)
    is_blacklisted = models.BooleanField(default=False)
    is_on_hold = models.BooleanField(default=False)
    cv_summary = models.TextField('AI CV Summary', blank=True, null=True,
                                  help_text='Short AI-generated summary of the resume.')

    # AI match score against the mapped vacancy's JD (0-100). Never computed for
    # "General Application" - see candidates/match_scoring.py. match_breakdown
    # holds the raw JSON (sub-scores + matched/missing skills) for the detail page.
    match_score = models.PositiveSmallIntegerField(blank=True, null=True)
    match_breakdown = models.TextField(blank=True, null=True)
    match_rationale = models.TextField(blank=True, null=True)
    match_state = models.CharField(max_length=10, choices=MatchState.choices, default=MatchState.PENDING)
    match_error = models.TextField(blank=True, null=True)
    match_scored_at = models.DateTimeField(blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.full_name} ({self.email})"

    def save(self, *args, **kwargs):
        if not self.candidate_code:
            self.candidate_code = generate_candidate_code()
        self.source = canonical_source(self.source)
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse('candidate_timeline', args=[self.pk])

    @property
    def status_label(self):
        """What the status is called on screen. A hold is named after the stage
        it was taken at, so the badge reads "Interview Hold", not just "Hold"."""
        if self.status == self.Status.SCREENING_HOLD:
            return hold_label(self.hold_from_status)
        return self.get_status_display()

    @property
    def resume_action(self):
        """The move offered to a held candidate: the stage after the one they
        were held at, so a Round 1 Hold is taken off hold into Interview.
        Falls back to Shortlisted when the stage was never recorded."""
        url_name, stage = HOLD_RESUME_ACTIONS.get(
            self.hold_from_status, HOLD_RESUME_ACTIONS[self.Status.OPEN])
        return {'url_name': url_name, 'stage': stage, 'label': f'Move to {stage}'}

    @property
    def email_is_placeholder(self):
        """True when no real email was found (bulk upload / unparsed CV) - the
        row carries a pending-*@placeholder.local stand-in for HR to replace."""
        return (self.email or '').endswith(f'@{PLACEHOLDER_EMAIL_DOMAIN}')

    @property
    def latest_interview(self):
        """Most recent interview (uses the prefetch cache when available)."""
        ivs = list(self.interviews.all())
        return ivs[-1] if ivs else None

    @property
    def match_breakdown_parsed(self):
        if not self.match_breakdown:
            return {}
        try:
            return json.loads(self.match_breakdown)
        except ValueError:
            return {}


# A hold taken at the Open stage is what this app has always called a
# "Screening Hold"; every other stage simply names itself.
HOLD_STAGE_NAMES = {Candidate.Status.OPEN: 'Screening'}

# The pipeline stages a hold can be taken at, and where lifting it goes: back
# into the pipeline at the next stage, so a Round 1 Hold resumes at Interview.
HOLD_RESUME_ACTIONS = {
    Candidate.Status.OPEN: ('candidate_shortlist', 'Shortlisted'),
    Candidate.Status.SHORTLISTED: ('candidate_round1', 'Round 1'),
    Candidate.Status.ROUND1: ('candidate_interview_stage', 'Interview'),
    Candidate.Status.INTERVIEW: ('candidate_final_selection', 'Final Selection'),
    Candidate.Status.FINAL_SELECTION: ('candidate_hire', 'Hired'),
}
HOLD_STAGES = tuple(HOLD_RESUME_ACTIONS)


def hold_label(from_status):
    """Name a hold after the stage it was taken at: held after an interview
    reads "Interview Hold". Plain "Hold" when the stage was never recorded."""
    plain = Candidate.Status.SCREENING_HOLD.label
    if not from_status or from_status == Candidate.Status.SCREENING_HOLD:
        return plain
    stage = (HOLD_STAGE_NAMES.get(from_status)
             or dict(Candidate.Status.choices).get(from_status, from_status))
    return f'{stage} {plain}'


class CandidateEducation(models.Model):
    candidate = models.ForeignKey(Candidate, on_delete=models.CASCADE, related_name='education')
    qualification = models.CharField(max_length=255)
    institution = models.CharField(max_length=255, blank=True, null=True)
    year_completed = models.PositiveIntegerField(blank=True, null=True)
    percentage = models.DecimalField(max_digits=5, decimal_places=2, blank=True, null=True)
    specialization = models.CharField(max_length=255, blank=True, null=True)

    class Meta:
        ordering = ['-year_completed']

    def __str__(self):
        return f"{self.qualification} - {self.institution or ''}"


class CandidateExperience(models.Model):
    candidate = models.ForeignKey(Candidate, on_delete=models.CASCADE, related_name='experience_set')
    company_name = models.CharField(max_length=255)
    designation = models.CharField(max_length=255, blank=True, null=True)
    start_date = models.DateField(blank=True, null=True)
    end_date = models.DateField(blank=True, null=True)
    total_experience = models.DecimalField(max_digits=5, decimal_places=1, blank=True, null=True)
    skills = models.CharField(max_length=500, blank=True, null=True)

    class Meta:
        ordering = ['-start_date']

    def __str__(self):
        return f"{self.designation or ''} @ {self.company_name}"


class CandidateStatusHistory(models.Model):
    candidate = models.ForeignKey(Candidate, on_delete=models.CASCADE, related_name='history')
    old_status = models.CharField(max_length=30, blank=True)
    new_status = models.CharField(max_length=30)
    changed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    performed_by = models.CharField('Done by', max_length=150, blank=True, null=True,
                                    help_text='Who actually performed this action (free text).')
    remarks = models.TextField(blank=True, null=True)
    # default (not auto_now_add) so historical dates can be backfilled on import
    changed_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-changed_at']
        verbose_name_plural = 'Candidate status histories'

    def __str__(self):
        return f"{self.candidate.full_name}: {self.old_status} -> {self.new_status}"


class Blacklist(models.Model):
    candidate = models.ForeignKey(Candidate, on_delete=models.CASCADE, related_name='blacklist_entries')
    reason = models.TextField()
    blacklisted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    blacklisted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-blacklisted_at']

    def __str__(self):
        return f"{self.candidate.full_name} blacklisted on {self.blacklisted_at:%Y-%m-%d}"


class EmailRegistry(models.Model):
    """Fast duplicate-detection lookup: one row per unique applicant email."""
    email = models.EmailField(unique=True)
    first_candidate = models.ForeignKey(
        Candidate, on_delete=models.SET_NULL, null=True, blank=True, related_name='+'
    )
    application_count = models.PositiveIntegerField(default=1)
    last_applied_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.email} ({self.application_count} applications)"


class Note(models.Model):
    """Free-text HR notes on a candidate, e.g. "Strong communication,
    salary expectation high"."""
    candidate = models.ForeignKey(Candidate, on_delete=models.CASCADE, related_name='notes')
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    text = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Note on {self.candidate.full_name} by {self.author}"


class CommunicationLog(models.Model):
    class Channel(models.TextChoices):
        PHONE = 'PHONE', 'Phone'
        INTERVIEW = 'INTERVIEW', 'Interview'
        EMAIL = 'EMAIL', 'Email'
        OTHER = 'OTHER', 'Other'

    class Outcome(models.TextChoices):
        UNABLE = 'UNABLE', 'Unable to connect'
        CALLBACK = 'CALLBACK', 'Call back'
        NOT_TURNED_UP = 'NOT_TURNED_UP', 'Not turned up'
        ATTENDED = 'ATTENDED', 'Attended'
        SHORTLISTED = 'SHORTLISTED', 'Shortlisted after call'
        NOT_SHORTLISTED = 'NOT_SHORTLISTED', 'Not shortlisted'

    candidate = models.ForeignKey(Candidate, on_delete=models.CASCADE, related_name='communication_logs')
    channel = models.CharField(max_length=20, choices=Channel.choices, default=Channel.EMAIL)
    outcome = models.CharField(max_length=20, choices=Outcome.choices, blank=True, null=True)
    subject = models.CharField(max_length=255, blank=True, null=True)
    message = models.TextField(blank=True, null=True)
    logged_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    # default (not auto_now_add) so the real call date can be set on import
    logged_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-logged_at']

    def __str__(self):
        return f"{self.get_channel_display()} with {self.candidate.full_name} on {self.logged_at:%Y-%m-%d}"


class Attachment(models.Model):
    """Extra documents beyond the resume (ID proof, certificates, etc.)."""
    candidate = models.ForeignKey(Candidate, on_delete=models.CASCADE, related_name='attachments')
    file = models.FileField(upload_to='attachments/')
    label = models.CharField(max_length=255, blank=True, null=True)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-uploaded_at']

    def __str__(self):
        return self.label or self.file.name


def bulk_cv_path(instance, filename):
    """bulk_cvs/<batch_id>/<uuid>_<original name> - the uploaded file is kept
    even when parsing fails, so HR can retry the same file later."""
    stem = os.path.basename(filename)[-120:]
    return f"bulk_cvs/{instance.batch_id or 'pending'}/{uuid.uuid4().hex[:8]}_{stem}"


class BulkUploadBatch(models.Model):
    """One "Bulk Upload CV" submission: the vacancy + source HR picked, plus one
    BulkUploadItem per file. Candidates are only created for items that parse
    successfully, so a failed CV never leaves a half-filled row in the
    repository."""
    job = models.ForeignKey(Job, on_delete=models.SET_NULL, null=True, blank=True, related_name='bulk_batches')
    source = models.CharField(max_length=255, blank=True, null=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    performed_by = models.CharField('Done by', max_length=150, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Bulk upload #{self.pk} ({self.job.title if self.job else 'no vacancy'})"

    @property
    def is_finished(self):
        return not self.items.filter(
            status__in=[BulkUploadItem.Status.PENDING, BulkUploadItem.Status.PARSING]
        ).exists()


class BulkUploadItem(models.Model):
    """One CV inside a batch, tracked from upload through parsing to the
    candidate row it produced (or the error that stopped it)."""
    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Waiting'
        PARSING = 'PARSING', 'Parsing'
        SUCCESS = 'SUCCESS', 'Added'
        ERROR = 'ERROR', 'Failed'

    batch = models.ForeignKey(BulkUploadBatch, on_delete=models.CASCADE, related_name='items')
    filename = models.CharField(max_length=255)
    cv_file = models.FileField(upload_to=bulk_cv_path)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    # Non-fatal note, e.g. "no email found in the CV" - the candidate is still created.
    warning = models.CharField(max_length=255, blank=True, null=True)
    error_message = models.TextField(blank=True, null=True)
    # Raw JSON the Logic App returned, kept for troubleshooting. TextField rather
    # than JSONField so the Azure SQL backend needs no JSON column support.
    parsed_json = models.TextField(blank=True, null=True)
    candidate = models.ForeignKey(Candidate, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    attempts = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['pk']

    def __str__(self):
        return f"{self.filename} ({self.get_status_display()})"

    @property
    def parsed(self):
        if not self.parsed_json:
            return {}
        try:
            return json.loads(self.parsed_json)
        except ValueError:
            return {}


class Offer(models.Model):
    class Status(models.TextChoices):
        DRAFT = 'DRAFT', 'Draft'
        SENT = 'SENT', 'Sent'
        ACCEPTED = 'ACCEPTED', 'Accepted'
        DECLINED = 'DECLINED', 'Declined'

    candidate = models.ForeignKey(Candidate, on_delete=models.CASCADE, related_name='offers')
    offer_letter = models.FileField(upload_to='offers/', blank=True, null=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    sent_at = models.DateTimeField(blank=True, null=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Offer for {self.candidate.full_name} ({self.get_status_display()})"
