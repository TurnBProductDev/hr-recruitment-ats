import secrets
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils import timezone

from candidates.models import Candidate

# How long a slot an interview occupies on the interviewer's calendar, for the
# double-booking check below. Also the .ics event length in interviews/invites.py,
# and the length of a slot an interviewer proposes in InterviewSlot below - keep
# all three in sync since they describe the same meeting block.
INTERVIEW_DURATION = timedelta(minutes=30)

# How long a candidate's "pick your interview slot" link stays usable, counted
# from whenever the interviewer (most recently) proposed slots. Also how long
# the proposed-but-unpicked slots stay held against the interviewer's other
# interviews - see InterviewSlot.held_conflicts_for.
CANDIDATE_SLOT_LINK_HOURS = 48


class Interview(models.Model):
    class RoundType(models.TextChoices):
        ROUND1 = 'ROUND1', 'Round 1'
        # Plain "Round 2" - what Allocate Interviewer/Manual Slot Allocate
        # actually offer now (see interviews.forms._simplified_round_type_choices).
        # TECHNICAL/MANAGERIAL/FINAL/HR below predate this: HR could label
        # *what kind* of Round 2 it was, but offering all 4 alongside Round 1
        # just read as "a lot of rounds" with no clear meaning - kept only so
        # existing interviews/requests with one of them still work.
        ROUND2 = 'ROUND2', 'Round 2'
        TECHNICAL = 'TECHNICAL', 'Technical'
        MANAGERIAL = 'MANAGERIAL', 'Managerial'
        FINAL = 'FINAL', 'Final'
        HR = 'HR', 'HR Round'

    class Mode(models.TextChoices):
        ONSITE = 'ONSITE', 'Onsite'
        PHONE = 'PHONE', 'Phone'
        VIDEO = 'VIDEO', 'Video'

    class Status(models.TextChoices):
        SCHEDULED = 'SCHEDULED', 'Scheduled'
        COMPLETED = 'COMPLETED', 'Completed'
        CANCELLED = 'CANCELLED', 'Cancelled'
        RESCHEDULED = 'RESCHEDULED', 'Rescheduled'

    class Result(models.TextChoices):
        PASS_ = 'PASS', 'Move to Next Round'
        FAIL = 'FAIL', 'Reject'
        PENDING = 'PENDING', 'Pending'
        # A transient pick on the Mark Result form only - a Hold pauses the
        # candidate, it doesn't decide the interview, so it's never the value
        # actually saved: InterviewResultView.form_valid puts the interview
        # back to Pending right after applying the hold (see
        # candidates.views._settle_round_interview for the same convention
        # from the Hiring block's own Hold action).
        HOLD = 'HOLD', 'Hold'

    # An interview is "open" while it is still waiting for a result. A candidate
    # may only have one open interview at a time - see open_for().
    OPEN_STATUSES = (Status.SCHEDULED, Status.RESCHEDULED)

    candidate = models.ForeignKey(Candidate, on_delete=models.CASCADE, related_name='interviews')
    round_type = models.CharField(max_length=20, choices=RoundType.choices, default=RoundType.ROUND1)
    interviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='interviews'
    )
    scheduled_date = models.DateTimeField()
    mode = models.CharField(max_length=20, choices=Mode.choices, default=Mode.VIDEO)
    meeting_link = models.URLField(blank=True, null=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.SCHEDULED)
    result = models.CharField(max_length=20, choices=Result.choices, default=Result.PENDING)
    feedback = models.TextField(blank=True, null=True)
    score = models.DecimalField(max_digits=4, decimal_places=1, blank=True, null=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+'
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    # Set only by InterviewCancelView, cleared on un-cancel - lets
    # CandidateRevertLastActionView tell whether a Cancel is the most recent
    # thing that happened to this candidate (nothing else stamps a "when" for
    # it - see that view's docstring).
    cancelled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['scheduled_date']

    def __str__(self):
        return f"{self.candidate.full_name} - {self.get_round_type_display()} on {self.scheduled_date:%Y-%m-%d %H:%M}"

    def get_absolute_url(self):
        return reverse('interview_detail', args=[self.pk])

    @classmethod
    def open_for(cls, candidate):
        """Interviews of this candidate that are still awaiting a result.

        One candidate can only have one of these at a time: no second interview
        may be scheduled - not even for a different role - until the result of
        the open one is marked.
        """
        return cls.objects.filter(candidate=candidate, status__in=cls.OPEN_STATUSES,
                                  result=cls.Result.PENDING)

    @classmethod
    def conflicts_for(cls, interviewer, scheduled_date, exclude_pk=None):
        """Other open interviews for this interviewer whose slot (see
        INTERVIEW_DURATION) overlaps scheduled_date - stops HR from double-
        booking the same interviewer within this app. This can't see anything
        on the interviewer's actual Outlook calendar (that needs the Graph API
        integration); it only catches clashes between interviews scheduled
        through the ATS itself.
        """
        if interviewer is None:
            return cls.objects.none()
        start, end = scheduled_date, scheduled_date + INTERVIEW_DURATION
        qs = cls.objects.filter(
            interviewer=interviewer, status__in=cls.OPEN_STATUSES,
            scheduled_date__lt=end, scheduled_date__gt=start - INTERVIEW_DURATION,
        )
        if exclude_pk:
            qs = qs.exclude(pk=exclude_pk)
        return qs


class InterviewReschedule(models.Model):
    """One row per reschedule.

    Rescheduling rewrites the interview row in place - there is only ever one
    row per interview - so this is what keeps the trail of what moved, when and
    who moved it, and it is what feeds the candidate's activity history.
    """
    interview = models.ForeignKey(Interview, on_delete=models.CASCADE, related_name='reschedules')
    previous_date = models.DateTimeField()
    new_date = models.DateTimeField()
    previous_interviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    new_interviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    changed_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ['-changed_at']

    def __str__(self):
        return f"{self.interview_id}: {self.summary}"

    @staticmethod
    def _name(user):
        if not user:
            return 'Unassigned'
        return user.get_full_name() or user.get_username()

    @property
    def summary(self):
        """What changed, as one line for the activity feed."""
        parts = []
        if self.previous_date != self.new_date:
            parts.append(f'Date moved from {timezone.localtime(self.previous_date):%d %b %Y %H:%M} '
                         f'to {timezone.localtime(self.new_date):%d %b %Y %H:%M}.')
        if self.previous_interviewer_id != self.new_interviewer_id:
            parts.append(f'Interviewer changed from {self._name(self.previous_interviewer)} '
                         f'to {self._name(self.new_interviewer)}.')
        return ' '.join(parts)


class InterviewRequest(models.Model):
    """The interviewer-proposes-slots scheduling flow: HR allocates an
    interviewer to a candidate (no date yet), the interviewer proposes 2-3
    one-hour slots, the candidate picks one via an emailed link
    (candidate_token), and HR approves it (or asks for a reschedule) - at
    which point a real Interview row (above) is created with that date. This
    row tracks that in-between state; once approved it just sits there as
    SCHEDULED, linked to the Interview it produced, for history."""
    class Status(models.TextChoices):
        AWAITING_SLOTS = 'AWAITING_SLOTS', 'Awaiting Slots'
        AWAITING_SELECTION = 'AWAITING_SELECTION', 'Awaiting Candidate Selection'
        AWAITING_HR_APPROVAL = 'AWAITING_HR_APPROVAL', 'Awaiting HR Approval'
        SCHEDULED = 'SCHEDULED', 'Scheduled'
        CANCELLED = 'CANCELLED', 'Cancelled'

    # These three count as "still in progress" - see open_for().
    OPEN_STATUSES = (Status.AWAITING_SLOTS, Status.AWAITING_SELECTION, Status.AWAITING_HR_APPROVAL)

    candidate = models.ForeignKey(Candidate, on_delete=models.CASCADE, related_name='interview_requests')
    round_type = models.CharField(max_length=20, choices=Interview.RoundType.choices, default=Interview.RoundType.ROUND1)
    interviewer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='interview_requests')
    mode = models.CharField(max_length=20, choices=Interview.Mode.choices, default=Interview.Mode.VIDEO)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.AWAITING_SLOTS)
    interview = models.ForeignKey(
        Interview, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+'
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    # The candidate's "pick your slot" link is this token, not the row's own
    # pk - a bearer credential, regenerated every time slots are (re-)proposed
    # so an old emailed link can never resurface and match a later round of
    # slots. Blank until slots are first proposed.
    candidate_token = models.CharField(max_length=50, unique=True, blank=True, null=True, db_index=True)
    # Set (and reset) every time slots move to AWAITING_SELECTION - the
    # candidate's link, and the hold on every proposed slot (see
    # InterviewSlot.held_conflicts_for), are only valid for
    # CANDIDATE_SLOT_LINK_HOURS from this timestamp.
    slots_proposed_at = models.DateTimeField(null=True, blank=True)
    candidate_selected_slot = models.ForeignKey(
        'InterviewSlot', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    candidate_selected_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.candidate.full_name} - {self.get_round_type_display()} ({self.get_status_display()})"

    @classmethod
    def open_for(cls, candidate):
        """This candidate's requests still in progress (awaiting slots,
        awaiting the candidate's pick, or awaiting HR's approval) - mirrors
        Interview.open_for so a candidate can't have both an open request and
        an open interview at once."""
        return cls.objects.filter(candidate=candidate, status__in=cls.OPEN_STATUSES)

    def new_candidate_token(self):
        """Generate and set a fresh bearer token for the candidate's slot-pick
        link. Doesn't save - the caller is already about to save alongside
        the other propose-slots fields."""
        self.candidate_token = secrets.token_urlsafe(32)
        return self.candidate_token

    @property
    def candidate_link_expired(self):
        if not self.slots_proposed_at:
            return True
        return timezone.now() > self.slots_proposed_at + timedelta(hours=CANDIDATE_SLOT_LINK_HOURS)


class InterviewSlot(models.Model):
    """One of the 2-3 one-hour times an interviewer proposed for an
    InterviewRequest. The candidate picking one (see the public
    candidate_slot_pick view) records it as the request's
    candidate_selected_slot; HR approving that turns it into the actual
    Interview's scheduled_date. The rest are left behind, unpicked."""
    request = models.ForeignKey(InterviewRequest, on_delete=models.CASCADE, related_name='slots')
    start_datetime = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['start_datetime']

    def __str__(self):
        return f"{self.request_id}: {self.start_datetime:%Y-%m-%d %H:%M}"

    @classmethod
    def held_conflicts_for(cls, interviewer, scheduled_date, exclude_request_pk=None):
        """Slots currently "holding" this interviewer's time against a
        pending candidate-selection flow, overlapping scheduled_date - every
        still-unpicked proposed slot while its link is still valid, plus
        whichever single slot a candidate has already picked (held
        indefinitely, until HR approves it into a real Interview or asks for
        a reschedule). Checked alongside Interview.conflicts_for wherever an
        interview gets booked (proposing slots, manual/direct scheduling,
        rescheduling), so none of those paths can double-book over a request
        this one hasn't resolved yet."""
        if interviewer is None:
            return cls.objects.none()
        start, end = scheduled_date, scheduled_date + INTERVIEW_DURATION
        cutoff = timezone.now() - timedelta(hours=CANDIDATE_SLOT_LINK_HOURS)
        held = (
            models.Q(request__status=InterviewRequest.Status.AWAITING_SELECTION,
                     request__slots_proposed_at__gt=cutoff)
            | models.Q(request__status=InterviewRequest.Status.AWAITING_HR_APPROVAL,
                      request__candidate_selected_slot=models.F('pk'))
        )
        qs = cls.objects.filter(
            held, request__interviewer=interviewer,
            start_datetime__lt=end, start_datetime__gt=start - INTERVIEW_DURATION,
        )
        if exclude_request_pk:
            qs = qs.exclude(request_id=exclude_request_pk)
        return qs


def candidate_round_label(round_type):
    """How a round is named to the CANDIDATE (the slot-pick email/page) -
    deliberately different from the internal round_type/"Round 1"/"Round 2"
    naming HR sees everywhere else. Round 1 (ROUND1) is always Technical
    Round; every Round 2 sub-type (Technical/Managerial/Final/HR - see
    candidates.views.ROUND2_TYPES) is shown as HR Round regardless of which
    one HR actually picked - candidates never see the internal round_type
    choice at all, only which of the two candidate-facing rounds this is."""
    if round_type == Interview.RoundType.ROUND1:
        return 'Technical Round'
    return 'HR Round'


def open_interview_message(interview):
    """Why a new interview was refused, phrased for the HR user."""
    when = timezone.localtime(interview.scheduled_date)
    return (f'{interview.candidate.full_name} already has an open '
            f'{interview.get_round_type_display()} interview on {when:%d %b %Y %H:%M}. '
            f'Update that interview’s status (mark the result) before scheduling '
            f'another one.')


def open_interview_request_message(request):
    """Why a new allocation was refused, phrased for the HR user."""
    waiting_on = {
        InterviewRequest.Status.AWAITING_SLOTS: 'the interviewer to propose slots',
        InterviewRequest.Status.AWAITING_SELECTION: 'the candidate to pick a slot',
        InterviewRequest.Status.AWAITING_HR_APPROVAL: 'you to approve the candidate\'s pick',
    }.get(request.status, 'this to resolve')
    return (f'{request.candidate.full_name} already has an interviewer allocated '
            f'({request.interviewer.get_full_name() or request.interviewer.get_username()}) for '
            f'{request.get_round_type_display()}, awaiting {waiting_on}.')


def interviewer_conflict_message(interview):
    """Why a schedule/reschedule was refused for double-booking the interviewer."""
    when = timezone.localtime(interview.scheduled_date)
    name = interview.interviewer.get_full_name() or interview.interviewer.get_username()
    return (f'{name} is already interviewing {interview.candidate.full_name} '
            f'at {when:%d %b %Y %H:%M}. Pick a different time or interviewer.')


def held_slot_conflict_message(slot):
    """Why a schedule/reschedule/slot-proposal was refused for overlapping a
    slot currently held by a pending candidate-selection flow - see
    InterviewSlot.held_conflicts_for."""
    when = timezone.localtime(slot.start_datetime)
    request = slot.request
    name = request.interviewer.get_full_name() or request.interviewer.get_username()
    return (f'{name} has a proposed/selected interview slot with '
            f'{request.candidate.full_name} at {when:%d %b %Y %H:%M} that is still pending. '
            f'Pick a different time or interviewer.')
