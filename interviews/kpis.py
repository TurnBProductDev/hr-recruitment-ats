"""Interview KPI buckets (Today / Upcoming / Result Pending / Overdue).

One definition shared by the Summary tab's Interview strip + "Interviews by
Role" table (dashboard.views) and the interview list each tile drills into
(interviews.views.InterviewSchedulerListView), so a count and its list can
never disagree.

Only interviews for the round the candidate is currently at count - an R1
interview while they're at Round 1, a later-round interview while they're at
Round 2 - the same scoping candidates.flows uses for the Overview funnel's
Scheduled / Decision Pending, so a candidate already Rejected/Held straight
from the Hiring block doesn't leave a dangling interview counted here.
"""
from django.db.models import Count, Q
from django.utils import timezone

from candidates.models import Candidate
from interviews.models import Interview

S = Candidate.Status
R1 = Interview.RoundType.ROUND1
NON_R1 = [t for t, _ in Interview.RoundType.choices if t != R1]

AT_ACTIVE_STAGE = (Q(round_type=R1, candidate__status=S.ROUND1)
                   | Q(round_type__in=NON_R1, candidate__status=S.INTERVIEW))

# (key, label, accent colour, one-line explanation shown under the tile)
BUCKETS = [
    ('today', 'Today', '#0e6f6b', 'Booked for today'),
    ('upcoming', 'Upcoming', '#3d5f86', 'All future interviews'),
    ('result_pending', 'Result Pending', '#33393c', 'Marked done, no result yet'),
    ('overdue', 'Overdue', '#b02a37', 'Time passed, not marked done'),
]
BUCKET_LABELS = {k: label for k, label, _, _ in BUCKETS}


def bucket_q(bucket, now=None):
    """Q for one bucket, on an Interview queryset (or inside Count(filter=))."""
    now = now or timezone.now()
    still_open = Q(status__in=Interview.OPEN_STATUSES)
    if bucket == 'today':
        return still_open & Q(scheduled_date__date=timezone.localdate(now))
    if bucket == 'upcoming':
        return still_open & Q(scheduled_date__gte=now)
    if bucket == 'overdue':
        return still_open & Q(scheduled_date__lt=now)
    if bucket == 'result_pending':
        return Q(status=Interview.Status.COMPLETED, result=Interview.Result.PENDING)
    raise ValueError(bucket)


def active_interviews(qs=None):
    return (qs if qs is not None else Interview.objects.all()).filter(AT_ACTIVE_STAGE)


def _counts(now):
    """Count kwargs: <bucket>, <bucket>_r1, <bucket>_r2 for every bucket."""
    out = {}
    for key, _, _, _ in BUCKETS:
        q = bucket_q(key, now)
        out[key] = Count('id', filter=q)
        out[key + '_r1'] = Count('id', filter=q & Q(round_type=R1))
        out[key + '_r2'] = Count('id', filter=q & Q(round_type__in=NON_R1))
    return out


def strip(interviews, now=None):
    """The 4 KPI tiles, in display order."""
    agg = interviews.aggregate(**_counts(now or timezone.now()))
    return [{'key': k, 'label': label, 'accent': accent, 'hint': hint,
             'total': agg[k], 'r1': agg[k + '_r1'], 'r2': agg[k + '_r2']}
            for k, label, accent, hint in BUCKETS]


def by_role(interviews, now=None):
    """Role rows (by job title), each with its vacancies (job codes) nested
    under `vacancies`. Roles/vacancies with nothing in any bucket are left
    out; roles ordered by their busiest-first total."""
    now = now or timezone.now()
    keys = [k for k, _, _, _ in BUCKETS]
    rows = (interviews
            .values('candidate__job_id', 'candidate__job__title', 'candidate__job__job_code',
                    'candidate__job__location')
            .annotate(**{k: Count('id', filter=bucket_q(k, now)) for k in keys})
            .order_by('candidate__job__title', 'candidate__job__job_code'))
    roles = {}
    for r in rows:
        if not any(r[k] for k in keys):
            continue
        title = r['candidate__job__title'] or 'Unassigned'
        role = roles.setdefault(title, {'title': title, 'vacancies': [], **{k: 0 for k in keys}})
        for k in keys:
            role[k] += r[k]
        role['vacancies'].append({
            'job_id': r['candidate__job_id'], 'code': r['candidate__job__job_code'] or '-',
            'location': r['candidate__job__location'] or '', **{k: r[k] for k in keys},
        })
    return sorted(roles.values(), key=lambda role: -sum(role[k] for k in keys))
