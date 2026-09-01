from django.db.models import Avg, Count, DurationField, ExpressionWrapper, F, Q, Sum
from django.utils import timezone
from django.views.generic import TemplateView

from candidates.flows import flow_count
from candidates.models import Candidate, CandidateStatusHistory
from candidates.permissions import ANY_STAFF, GroupRequiredMixin
from interviews.models import Interview
from jobs.models import Job

STATUS = Candidate.Status


# The By Job/Role and By Source tables roll the nine statuses into four buckets
# so the columns always add up to Total. Every status belongs to exactly one
# bucket - add any new status here or the row stops balancing.
OPEN_GROUP = (STATUS.OPEN, STATUS.SCREENING_HOLD)
SHORTLISTED_GROUP = (STATUS.SHORTLISTED, STATUS.ROUND1, STATUS.INTERVIEW,
                     STATUS.FINAL_SELECTION)
REJECTED_GROUP = (STATUS.REJECTED, STATUS.BLACKLISTED)
HIRED_GROUP = (STATUS.HIRED,)

# Composition-bar segment colors, shared by the top summary panel and every
# By Job / By Source row (dashboard Summary tab).
BUCKET_COLORS = {'open': '#0e6f6b', 'shortlisted': '#4cbdb3', 'rejected': '#d9534f', 'hired': '#1d3f4a'}


def _grouped_counts():
    """Count kwargs shared by the summary panel and the two breakdown tables."""
    return dict(
        total=Count('id'),
        open=Count('id', filter=Q(status__in=OPEN_GROUP)),
        shortlisted=Count('id', filter=Q(status__in=SHORTLISTED_GROUP)),
        rejected=Count('id', filter=Q(status__in=REJECTED_GROUP)),
        hired=Count('id', filter=Q(status__in=HIRED_GROUP)),
    )


def _with_bucket_pct(row):
    """Attach each bucket's share of the row's total, as whole percents, plus
    a shortlist-rate figure. Used for both the composition bars and the
    single-row summary strip."""
    total = row['total'] or 1
    for key in ('open', 'shortlisted', 'rejected', 'hired'):
        row[f'{key}_pct'] = round(row[key] / total * 100)
    row['shortlist_rate'] = round(row['shortlisted'] / total * 100) if row['total'] else 0
    return row


def _summary_counts_qs(qs):
    """Total / Open / Shortlisted / Rejected / Hired for a candidate queryset (one query),
    with each bucket's percent share of the total for the composition bar."""
    return _with_bucket_pct(qs.aggregate(**_grouped_counts()))


class HRDashboardView(GroupRequiredMixin, TemplateView):
    template_name = 'dashboard/dashboard.html'
    allowed_groups = ANY_STAFF

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        job_id = self.request.GET.get('job') or ''
        scope = self.request.GET.get('scope', '')
        base = Candidate.objects.all()
        if job_id:
            base = base.filter(job_id=job_id)
        if scope == 'open':   # only candidates under currently-open vacancies
            base = base.filter(job__status=Job.Status.OPEN, job__is_archived=False)

        job_list = Job.objects.all().order_by('title')
        if scope == 'open':
            job_list = job_list.filter(status=Job.Status.OPEN, is_archived=False)
        ctx['jobs'] = job_list
        ctx['selected_job'] = job_id
        ctx['scope'] = scope
        ctx['view'] = self.request.GET.get('view', 'summary')

        # ---------- Summary ----------
        ctx['summary'] = _summary_counts_qs(base)
        ctx['bucket_colors'] = BUCKET_COLORS
        ctx['by_job'] = [_with_bucket_pct(dict(r)) for r in
                         base.values('job__title').annotate(**_grouped_counts()).order_by('-total')]
        ctx['by_source'] = [_with_bucket_pct(dict(r)) for r in
                            base.exclude(source__isnull=True).exclude(source='')
                            .values('source').annotate(**_grouped_counts()).order_by('-total')]

        # ---------- Overview: recruitment funnel (Power BI style). Counts come
        # from candidates.flows so a card and its drill-down list always match. ----------
        def fc(flow):
            return flow_count(base, flow)

        jobs_scope = (Job.objects.filter(pk=job_id) if job_id
                      else Job.objects.filter(status=Job.Status.OPEN, is_archived=False))
        requirement = jobs_scope.aggregate(s=Sum('openings'))['s'] or 0

        shortlisted = fc('ever_shortlisted')
        unfit = fc('unfit')
        s_after_call, unable = fc('shortlisted_after_call'), fc('unable_to_connect')
        yet_call, rej_call = fc('call_pending'), fc('rejected_after_call')
        to_recall, call_dp = fc('to_recall'), fc('call_decision_pending')
        r1_dp, r2_dp = fc('r1_decision_pending'), fc('r2_decision_pending')
        r1_cleared, r1_sched, r1_ns, r1_yet, rej_r1 = (
            fc('r1_cleared'), fc('r1_scheduled'), fc('r1_no_show'), fc('r1_yet'), fc('rejected_after_round1'))
        r2_cleared, r2_sched, r2_ns, r2_yet, rej_r2 = (
            fc('r2_cleared'), fc('r2_scheduled'), fc('r2_no_show'), fc('r2_yet'), fc('rejected_after_round2'))
        on_hold, hired, rej_final = fc('on_hold'), fc('hired'), fc('rejected_after_final')
        screening_hold = fc('screening_hold')

        ctx['funnel_top'] = {'total': base.count(), 'requirement': requirement, 'unfit': unfit}
        # Corner "total" = everyone who got a decision at that stage (cleared + rejected-there)
        ctx['funnel'] = [
            {'name': 'Screening',
             'pending': ('Screening Pending', fc('open'), 'open'),
             'cleared': ('Screened & Shortlisted', shortlisted, shortlisted + unfit, 'ever_shortlisted'),
             # Hold can be taken at any stage, so this counts every held
             # candidate, not only the ones held during screening.
             'drops': [('Hold', screening_hold, 'screening_hold')]},
            {'name': 'Call',
             'pending': ('Yet to Call', yet_call, 'call_pending'),
             'cleared': ('Shortlisted After Call', s_after_call, s_after_call + rej_call, 'shortlisted_after_call'),
             # To Re-call sits beside Yet to Call: both are still-to-reach candidates,
             # kept separate so Yet to Call stays "never attempted". Rejected-after-call
             # has no card of its own — it is already inside the "total" corner above.
             'pending2': ('To Re-call', to_recall, 'to_recall'),
             'drops': [('Decision Pending', call_dp, 'call_decision_pending'),
                       ('Unable to Connect', unable, 'unable_to_connect')]},
            {'name': 'Round 1',
             'pending': ('Yet to Schedule', r1_yet, 'r1_yet'),
             'cleared': ('Round 1 Cleared', r1_cleared, r1_cleared + rej_r1, 'r1_cleared'),
             'drops': [('Scheduled', r1_sched, 'r1_scheduled'), ('Not Turned Up', r1_ns, 'r1_no_show'),
                       ('Decision Pending', r1_dp, 'r1_decision_pending')]},
            {'name': 'Round 2',
             'pending': ('Yet to Schedule', r2_yet, 'r2_yet'),
             'cleared': ('Round 2 Cleared', r2_cleared, r2_cleared + rej_r2, 'r2_cleared'),
             'drops': [('Scheduled', r2_sched, 'r2_scheduled'), ('Not Turned Up', r2_ns, 'r2_no_show'),
                       ('Decision Pending', r2_dp, 'r2_decision_pending')]},
            {'name': 'Final Decision',
             'pending': ('On Hold', on_hold, 'on_hold'),
             'cleared': ('Hired', hired, hired + rej_r2 + rej_final, 'hired'),
             'drops': []},
        ]

        # Collapsed-accordion view of the same numbers: one bar per stage
        # (width relative to the first stage's cleared count, so the row
        # narrows the way a funnel should), a pass-rate badge, and every
        # pending/hold/drop count flattened into clickable chips for the
        # expanded breakdown.
        BAR_COLORS = ('#0e6f6b', '#1a8b84', '#2ba49b', '#4cbdb3', '#8ad6ce')
        peak = ctx['funnel'][0]['cleared'][1] or 1
        for i, stage in enumerate(ctx['funnel']):
            label, value, decided, flow = stage['cleared']
            chips = [{'label': stage['pending'][0], 'count': stage['pending'][1], 'flow': stage['pending'][2]}]
            if 'pending2' in stage:
                chips.append({'label': stage['pending2'][0], 'count': stage['pending2'][1], 'flow': stage['pending2'][2]})
            chips += [{'label': d[0], 'count': d[1], 'flow': d[2]} for d in stage['drops']]
            stage.update(
                value=value,
                value_label=label,
                value_flow=flow,
                pct=round(value / peak * 100),
                conv=round(value / decided * 100) if i > 0 and decided else None,
                chips=chips,
                color=BAR_COLORS[i % len(BAR_COLORS)],
            )

        ctx['upcoming_interviews'] = Interview.objects.select_related('candidate', 'interviewer').filter(
            status=Interview.Status.SCHEDULED, scheduled_date__gte=timezone.now()
        ).order_by('scheduled_date')[:10]
        return ctx


class ReportsView(GroupRequiredMixin, TemplateView):
    template_name = 'dashboard/reports.html'
    allowed_groups = ANY_STAFF

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        job_id = self.request.GET.get('job') or ''
        base = Candidate.objects.all()
        if job_id:
            base = base.filter(job_id=job_id)
        ctx['jobs'] = Job.objects.all().order_by('title')
        ctx['selected_job'] = job_id

        total = base.count()
        rejected = base.filter(status=STATUS.REJECTED).count()
        ctx['rejection_ratio'] = round(rejected / total * 100, 1) if total else 0

        jobs_qs = Job.objects.filter(pk=job_id) if job_id else Job.objects.all()
        total_jobs = jobs_qs.count()
        jobs_with_hire = jobs_qs.filter(candidates__status=STATUS.HIRED).distinct().count()
        ctx['fill_rate'] = round(jobs_with_hire / total_jobs * 100, 1) if total_jobs else 0

        hire_hist = CandidateStatusHistory.objects.filter(new_status=STATUS.HIRED)
        if job_id:
            hire_hist = hire_hist.filter(candidate__job_id=job_id)
        avg_duration = (
            hire_hist.annotate(duration=ExpressionWrapper(
                F('changed_at') - F('candidate__created_at'), output_field=DurationField()))
            .aggregate(avg=Avg('duration'))['avg']
        )
        ctx['avg_time_to_hire_days'] = round(avg_duration.total_seconds() / 86400, 1) if avg_duration else None

        # Source effectiveness: total, shortlisted (%), hired (conversion %).
        # distinct=True keeps counts correct despite the history join.
        rows = (base.exclude(source__isnull=True).exclude(source='')
                .values('source')
                .annotate(total=Count('id', distinct=True),
                          shortlisted=Count('id', filter=Q(history__new_status=STATUS.SHORTLISTED), distinct=True),
                          hired=Count('id', filter=Q(status=STATUS.HIRED), distinct=True))
                .order_by('-total'))
        data = []
        for r in rows:
            t = r['total'] or 0
            data.append({**r,
                         'shortlist_pct': round(r['shortlisted'] / t * 100, 1) if t else 0,
                         'conversion_pct': round(r['hired'] / t * 100, 1) if t else 0})
        ctx['source_effectiveness'] = data
        return ctx
