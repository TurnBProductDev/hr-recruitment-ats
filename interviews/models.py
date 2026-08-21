from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils import timezone

from candidates.models import Candidate


class Interview(models.Model):
    class RoundType(models.TextChoices):
        ROUND1 = 'ROUND1', 'Round 1'
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
        PASS_ = 'PASS', 'Pass'
        FAIL = 'FAIL', 'Fail'
        PENDING = 'PENDING', 'Pending'

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
    created_at = models.DateTimeField(auto_now_add=True)

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
    changed_at = models.DateTimeField(default=timezone.now)

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


def open_interview_message(interview):
    """Why a new interview was refused, phrased for the HR user."""
    when = timezone.localtime(interview.scheduled_date)
    return (f'{interview.candidate.full_name} already has an open '
            f'{interview.get_round_type_display()} interview on {when:%d %b %Y %H:%M}. '
            f'Update that interview’s status (mark the result) before scheduling '
            f'another one.')
