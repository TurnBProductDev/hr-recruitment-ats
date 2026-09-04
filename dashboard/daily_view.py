"""Daily View: how many candidate actions HR actually took on a given day
(or date range) - not where candidates currently stand (that's the Overview
funnel), but how much ground got covered that day.

Every number here comes from *when* something happened -
CandidateStatusHistory.changed_at, CommunicationLog.logged_at, and
Interview.created_at / InterviewReschedule.changed_at - so this can't reuse
candidates.flows (which reads current candidate/interview state with no
date dimension at all). See dashboard/views.py's HRDashboardView for how
this plugs into the "Daily View" tab, and DailyActionDrilldownView for the
per-column event list a bar's click drills into.

`_sources_for()` is the single place each column's definition lives: a list
of (queryset, date_field, action_label, candidate_path) tuples. compute()
just counts them (the bar chart); events() lists the actual rows (the
drill-through) - so the chart and its drill-through can never disagree.
"""
from datetime import timedelta

from django.db.models import Exists, OuterRef, Q
from django.utils import timezone
from django.utils.dateparse import parse_date

from candidates.models import Candidate, CandidateStatusHistory, CommunicationLog
from interviews.models import Interview, InterviewReschedule

S = Candidate.Status
TERMINAL = (S.REJECTED, S.BLACKLISTED)
NON_R1_ROUNDS = (Interview.RoundType.TECHNICAL, Interview.RoundType.MANAGERIAL,
                 Interview.RoundType.FINAL, Interview.RoundType.HR)

COLUMNS = [
    ('resumes', 'Resumes Received'),
    ('screened', 'Screened'),
    ('calls', 'Calls'),
    ('round1', 'Round 1 Actions'),
    ('round2', 'Round 2 Actions'),
    ('hired', 'Hired'),
]


def range_from_request(get_params):
    """(start, end) date objects from `daily_from`/`daily_to` query params,
    falling back to default_date_range() if either is missing or
    unparsable. Shared by the Daily View tab and its drill-through, so a
    bar's link and the tab it came from always agree on the range."""
    start = parse_date(get_params.get('daily_from', '') or '')
    end = parse_date(get_params.get('daily_to', '') or '')
    if start and end:
        return (start, end) if start <= end else (end, start)
    return default_date_range()


def default_date_range():
    """The day before the most recent recorded activity, not just
    "yesterday" - a quiet weekend right before today would otherwise
    default to an empty chart."""
    latest = _latest_activity_date()
    day = (latest or timezone.localdate()) - timedelta(days=1)
    return day, day


def _latest_activity_date():
    candidates = [
        CandidateStatusHistory.objects.order_by('-changed_at').values_list('changed_at', flat=True).first(),
        CommunicationLog.objects.order_by('-logged_at').values_list('logged_at', flat=True).first(),
        Interview.objects.order_by('-created_at').values_list('created_at', flat=True).first(),
        InterviewReschedule.objects.order_by('-changed_at').values_list('changed_at', flat=True).first(),
        Candidate.objects.order_by('-created_at').values_list('created_at', flat=True).first(),
    ]
    dates = [timezone.localtime(dt).date() for dt in candidates if dt]
    return max(dates) if dates else None


def _reached_by(stage):
    """Exists() clause for use inside .annotate() on a CandidateStatusHistory
    queryset: True if this candidate had an EARLIER history row reaching
    `stage` (changed_at strictly before this row's own changed_at) - i.e.
    "had they reached this stage by the time of this event", the same
    question candidates.flows asks of *current* status, asked instead of
    one specific moment in the past."""
    return Exists(CandidateStatusHistory.objects.filter(
        candidate_id=OuterRef('candidate_id'), new_status=stage,
        changed_at__lt=OuterRef('changed_at')))


def _history_qs(new_status, date_range, job_id, old_status=None):
    qs = CandidateStatusHistory.objects.filter(new_status=new_status, changed_at__date__range=date_range)
    if old_status:
        qs = qs.filter(old_status=old_status)
    if job_id:
        qs = qs.filter(candidate__job_id=job_id)
    return qs.select_related('candidate', 'candidate__job')


def _reject_qs(date_range, job_id, not_yet, reached=None):
    """REJECTED/BLACKLISTED history rows in `date_range` where the candidate
    had (optionally) already reached `reached`, but had NOT yet reached
    `not_yet` as of that rejection - candidates.flows' "unfit" /
    "rejected_after_roundN" logic, asked per-event instead of the
    candidate's current status."""
    qs = CandidateStatusHistory.objects.filter(new_status__in=TERMINAL, changed_at__date__range=date_range)
    if job_id:
        qs = qs.filter(candidate__job_id=job_id)
    qs = qs.annotate(advanced=_reached_by(not_yet)).filter(advanced=False)
    if reached:
        qs = qs.annotate(prior=_reached_by(reached)).filter(prior=True)
    return qs.select_related('candidate', 'candidate__job')


def _rejected_or_held_at_screening_qs(date_range, job_id):
    """CV-Screening dropouts on that day: rejected outright, or held before
    ever being screened. A screening-stage hold is tracked separately on the
    Future Prospects page, but counts here the same as a rejection - same
    treatment as the Overview funnel and Summary tab (see
    candidates.flows.screened_out / dashboard.views.INITIAL_HOLD)."""
    qs = CandidateStatusHistory.objects.filter(
        Q(new_status__in=TERMINAL) | Q(new_status=S.SCREENING_HOLD, old_status=S.OPEN),
        changed_at__date__range=date_range)
    if job_id:
        qs = qs.filter(candidate__job_id=job_id)
    qs = qs.annotate(advanced=_reached_by(S.SHORTLISTED)).filter(advanced=False)
    return qs.select_related('candidate', 'candidate__job')


def _comm_log_qs(outcome, date_range, job_id):
    qs = CommunicationLog.objects.filter(
        channel=CommunicationLog.Channel.PHONE, outcome=outcome, logged_at__date__range=date_range)
    if job_id:
        qs = qs.filter(candidate__job_id=job_id)
    return qs.select_related('candidate', 'candidate__job')


def _interview_created_qs(round_types, date_range, job_id):
    qs = Interview.objects.filter(round_type__in=round_types, created_at__date__range=date_range)
    if job_id:
        qs = qs.filter(candidate__job_id=job_id)
    return qs.select_related('candidate', 'candidate__job')


def _interview_rescheduled_qs(round_types, date_range, job_id):
    qs = InterviewReschedule.objects.filter(
        interview__round_type__in=round_types, changed_at__date__range=date_range)
    if job_id:
        qs = qs.filter(interview__candidate__job_id=job_id)
    return qs.select_related('interview__candidate', 'interview__candidate__job')


def _sources_for(column, date_range, job_id):
    """The (queryset, date_field, action_label, candidate_path) tuples that
    make up one Daily View column. `candidate_path` is how to reach the
    Candidate from a row of that queryset ('self' for the Candidate
    queryset itself, 'candidate' for a direct FK, 'interview.candidate' for
    InterviewReschedule)."""
    if column == 'resumes':
        qs = Candidate.objects.filter(created_at__date__range=date_range)
        if job_id:
            qs = qs.filter(job_id=job_id)
        return [(qs.select_related('job'), 'created_at', 'Resume Received', 'self')]

    if column == 'screened':
        return [
            (_history_qs(S.SHORTLISTED, date_range, job_id), 'changed_at', 'Screened & Qualified', 'candidate'),
            (_rejected_or_held_at_screening_qs(date_range, job_id), 'changed_at', 'Rejected at Screening', 'candidate'),
        ]

    if column == 'calls':
        return [
            (_history_qs(S.ROUND1, date_range, job_id), 'changed_at', 'Shortlisted After Call', 'candidate'),
            (_comm_log_qs(CommunicationLog.Outcome.UNABLE, date_range, job_id), 'logged_at', 'Unable to Connect', 'candidate'),
            (_comm_log_qs(CommunicationLog.Outcome.CALLBACK, date_range, job_id), 'logged_at', 'Call Back', 'candidate'),
        ]

    if column == 'round1':
        r1 = [Interview.RoundType.ROUND1]
        return [
            (_history_qs(S.INTERVIEW, date_range, job_id), 'changed_at', 'Round 1 Cleared', 'candidate'),
            (_interview_created_qs(r1, date_range, job_id), 'created_at', 'Round 1 Interview Scheduled', 'candidate'),
            (_interview_rescheduled_qs(r1, date_range, job_id), 'changed_at', 'Round 1 Interview Rescheduled', 'interview.candidate'),
            (_reject_qs(date_range, job_id, not_yet=S.INTERVIEW, reached=S.ROUND1), 'changed_at', 'Rejected after Round 1', 'candidate'),
            (_history_qs(S.SCREENING_HOLD, date_range, job_id, old_status=S.ROUND1), 'changed_at', 'Hold before Round 2', 'candidate'),
        ]

    if column == 'round2':
        return [
            (_history_qs(S.FINAL_SELECTION, date_range, job_id), 'changed_at', 'Round 2 Cleared', 'candidate'),
            (_interview_created_qs(NON_R1_ROUNDS, date_range, job_id), 'created_at', 'Round 2 Interview Scheduled', 'candidate'),
            (_interview_rescheduled_qs(NON_R1_ROUNDS, date_range, job_id), 'changed_at', 'Round 2 Interview Rescheduled', 'interview.candidate'),
            (_reject_qs(date_range, job_id, not_yet=S.FINAL_SELECTION, reached=S.INTERVIEW), 'changed_at', 'Rejected after Round 2', 'candidate'),
            (_history_qs(S.SCREENING_HOLD, date_range, job_id, old_status=S.INTERVIEW), 'changed_at', 'Hold before Final', 'candidate'),
        ]

    if column == 'hired':
        return [(_history_qs(S.HIRED, date_range, job_id), 'changed_at', 'Hired', 'candidate')]

    return []


def _resolve(obj, path):
    for part in path.split('.'):
        obj = getattr(obj, part)
    return obj


def compute(date_range, job_id=None):
    """date_range is (start_date, end_date), both inclusive `date` objects.
    Returns the 6 chart columns in display order. Each column also carries
    its `breakdown` - the same per-source counts a bar's tooltip shows -
    computed from the identical _sources_for() list so it can never
    disagree with the bar's own total."""
    results = []
    for key, label in COLUMNS:
        breakdown = [{'label': action_label, 'value': qs.count()}
                    for qs, _, action_label, _ in _sources_for(key, date_range, job_id)]
        total = sum(b['value'] for b in breakdown)
        results.append({'key': key, 'label': label, 'value': total, 'breakdown': breakdown})
    return results


def events(column, date_range, job_id=None):
    """Flat, chronological list of the individual events behind one Daily
    View column - the drill-through a bar click lands on. Each item has
    `candidate`, `when` (datetime) and `action` (human label); the row
    count always matches that column's bar value for the same range."""
    rows = []
    for qs, date_attr, action_label, candidate_path in _sources_for(column, date_range, job_id):
        for obj in qs:
            candidate = obj if candidate_path == 'self' else _resolve(obj, candidate_path)
            rows.append({'candidate': candidate, 'when': getattr(obj, date_attr), 'action': action_label})
    rows.sort(key=lambda r: r['when'], reverse=True)
    return rows
