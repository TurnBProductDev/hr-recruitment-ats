from django.db.models import Avg, Count, DurationField, ExpressionWrapper, F, Q, Sum
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.views.generic import TemplateView

from candidates.flows import flow_count
from candidates.models import Candidate, CandidateStatusHistory
from candidates.permissions import ANY_STAFF, GroupRequiredMixin
from interviews.models import Interview
from jobs.models import Job

from . import daily_view

STATUS = Candidate.Status


# The By Job/Role and By Source tables roll the nine statuses into four buckets
# so the columns always add up to Total. Every status belongs to exactly one
# bucket - add any new status here or the row stops balancing.
OPEN_GROUP = (STATUS.OPEN, STATUS.SCREENING_HOLD)
SHORTLISTED_GROUP = (STATUS.SHORTLISTED, STATUS.ROUND1, STATUS.INTERVIEW,
                     STATUS.FINAL_SELECTION)
REJECTED_GROUP = (STATUS.REJECTED, STATUS.BLACKLISTED)
HIRED_GROUP = (STATUS.HIRED,)


def _grouped_counts():
    """Count kwargs shared by the summary panel and the two breakdown tables."""
    return dict(
        total=Count('id'),
        open=Count('id', filter=Q(status__in=OPEN_GROUP)),
        shortlisted=Count('id', filter=Q(status__in=SHORTLISTED_GROUP)),
        rejected=Count('id', filter=Q(status__in=REJECTED_GROUP)),
        hired=Count('id', filter=Q(status__in=HIRED_GROUP)),
    )


def _summary_counts_qs(qs):
    """Total / Open / Shortlisted / Rejected / Hired for a candidate queryset (one query)."""
    return qs.aggregate(**_grouped_counts())


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
        # Open vacancies, not candidates - always the global count regardless of
        # the job/candidate filters above, so picking one role doesn't make it look
        # like there's only one opening.
        ctx['open_positions'] = Job.objects.filter(status=Job.Status.OPEN, is_archived=False).count()
        ctx['summary'] = _summary_counts_qs(base)
        ctx['by_job'] = (base.values('job__title')
                         .annotate(**_grouped_counts())
                         .order_by('-total'))
        ctx['by_source'] = (base.exclude(source__isnull=True).exclude(source='')
                            .values('source')
                            .annotate(**_grouped_counts())
                            .order_by('-total'))

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
        call_dp = fc('call_decision_pending')
        r1_dp, r2_dp = fc('r1_decision_pending'), fc('r2_decision_pending')
        r1_cleared, r1_sched, r1_ns, r1_yet, rej_r1 = (
            fc('r1_cleared'), fc('r1_scheduled'), fc('r1_no_show'), fc('r1_yet'), fc('rejected_after_round1'))
        r2_cleared, r2_sched, r2_ns, r2_yet, rej_r2 = (
            fc('r2_cleared'), fc('r2_scheduled'), fc('r2_no_show'), fc('r2_yet'), fc('rejected_after_round2'))
        on_hold, hired, rej_final = fc('on_hold'), fc('hired'), fc('rejected_after_final')
        # On-Hold-status carve-outs, one per stage, keyed to the status the
        # candidate was held from - so nobody who gets put on Hold disappears
        # from the funnel, and nobody is double-counted against both their
        # Hold chip and the "cleared" stage they'd already passed.
        hold_before_shortlist = fc('hold_before_shortlist')
        hold_before_round1 = fc('hold_before_round1')
        hold_before_round2 = fc('hold_before_round2')
        hold_before_final = fc('hold_before_final')
        hold_after_final = fc('hold_after_final')

        screening_pending = fc('open')
        ctx['funnel_top'] = {
            'total': base.count(), 'requirement': requirement, 'unfit': unfit,
            # Shown as their own card above the bar chart, not as a funnel
            # stage - these candidates haven't reached a screening decision
            # yet, so they're excluded from the bars entirely (see peak below).
            'screening_pending': screening_pending, 'hold': hold_before_shortlist,
        }
        # Corner "total" = everyone who got a decision at that stage (cleared + rejected-there)
        ctx['funnel'] = [
            {'name': 'Screened & Shortlisted',
             # No 'pending' entry here (see the funnel-building loop below) -
             # Screening Pending and Hold are the "screening_pending"/"hold"
             # card above, not a bar segment or a breakdown chip, since
             # they're not a screening *decision* yet.
             'pending': None,
             'cleared': ('Screened & Shortlisted', shortlisted, shortlisted + unfit, 'ever_shortlisted'),
             'drops': [('Rejected', unfit, 'unfit')]},
            {'name': 'Shortlisted After Call',
             'pending': ('Yet to Call', yet_call, 'call_pending'),
             'cleared': ('Shortlisted After Call', s_after_call, s_after_call + rej_call, 'shortlisted_after_call'),
             'drops': [('Decision Pending', call_dp, 'call_decision_pending'),
                       ('Unable to Connect', unable, 'unable_to_connect'),
                       ('Hold', hold_before_round1, 'hold_before_round1'),
                       ('Rejected', rej_call, 'rejected_after_call')]},
            {'name': 'Round 1 Cleared',
             'pending': ('Yet to Schedule', r1_yet, 'r1_yet'),
             'cleared': ('Round 1 Cleared', r1_cleared, r1_cleared + rej_r1, 'r1_cleared'),
             'drops': [('Scheduled', r1_sched, 'r1_scheduled'), ('Not Turned Up', r1_ns, 'r1_no_show'),
                       ('Decision Pending', r1_dp, 'r1_decision_pending'),
                       ('Hold', hold_before_round2, 'hold_before_round2'),
                       ('Rejected', rej_r1, 'rejected_after_round1')]},
            {'name': 'Round 2 Cleared',
             'pending': ('Yet to Schedule', r2_yet, 'r2_yet'),
             'cleared': ('Round 2 Cleared', r2_cleared, r2_cleared + rej_r2, 'r2_cleared'),
             'drops': [('Scheduled', r2_sched, 'r2_scheduled'), ('Not Turned Up', r2_ns, 'r2_no_show'),
                       ('Decision Pending', r2_dp, 'r2_decision_pending'),
                       ('Hold', hold_before_final, 'hold_before_final'),
                       ('Rejected', rej_r2, 'rejected_after_round2')]},
            {'name': 'Hired',
             'pending': ('On Hold', on_hold, 'on_hold'),
             'cleared': ('Hired', hired, hired + rej_r2 + rej_final, 'hired'),
             'drops': [('Hold', hold_after_final, 'hold_after_final'),
                       ('Rejected', rej_final, 'rejected_after_final')]},
        ]

        # One bar per stage, breakdown baked in as shaded segments: a solid
        # green segment for the stage's own "cleared" count, then grey
        # segments (darkest = biggest) for everyone from the previous stage's
        # cohort who didn't clear - pending + every drop reason, normalised so
        # they always fill the bar out to exactly the previous stage's share
        # of peak, however their raw counts add up.
        GREEN = '#0e6f6b'
        GREY_RGB = '31,42,42'  # this page's dark neutral (#1f2a2a), same as .val text
        GREY_ALPHAS = (0.14, 0.11, 0.08, 0.06, 0.04, 0.03)
        # Every bar's width - and the very first bar's own breakdown fill -
        # is now relative to "everyone who reached a screening decision"
        # (shortlisted + rejected-at-screening), not the total candidate
        # count, since Screening Pending/Hold no longer appear in the bars.
        peak = (shortlisted + unfit) or 1
        prev_value = shortlisted + unfit
        for stage in ctx['funnel']:
            label, value, _decided, flow = stage['cleared']
            breakdown_chips = []
            if stage['pending']:
                breakdown_chips.append({'label': stage['pending'][0], 'count': stage['pending'][1], 'flow': stage['pending'][2]})
            if 'pending2' in stage:
                breakdown_chips.append({'label': stage['pending2'][0], 'count': stage['pending2'][1], 'flow': stage['pending2'][2]})
            breakdown_chips += [{'label': d[0], 'count': d[1], 'flow': d[2]} for d in stage['drops']]
            # Keep a decimal instead of rounding to a whole percent, so two
            # small-but-different stages (e.g. 10 vs 1) don't collapse onto
            # the same rounded width. A nonzero stage still gets a minimum
            # sliver so it stays visible - and clickable - even when it
            # rounds under 1%.
            pct = round(value / peak * 100, 1)
            if value > 0 and pct < 1:
                pct = 1

            segments = []
            if value > 0:
                segments.append({
                    'flow': flow, 'value': value, 'label': label, 'pct': pct,
                    'bg': GREEN, 'text_color': '#fff', 'show_inline': pct > 6, 'is_green': True,
                })
            breakdown_items = sorted((c for c in breakdown_chips if c['count'] > 0), key=lambda c: -c['count'])
            breakdown_pct = round((prev_value - value) / peak * 100, 1)
            items_total = sum(c['count'] for c in breakdown_items) or 1
            for j, c in enumerate(breakdown_items):
                seg_pct = round(c['count'] / items_total * breakdown_pct, 1)
                if seg_pct < 0.3:
                    seg_pct = 0.3
                segments.append({
                    'flow': c['flow'], 'value': c['count'], 'label': c['label'], 'pct': seg_pct,
                    'bg': f'rgba({GREY_RGB},{GREY_ALPHAS[min(j, len(GREY_ALPHAS) - 1)]})',
                    'text_color': '#33393c', 'show_inline': seg_pct > 4.5, 'is_green': False,
                })

            # Chips shown when the row is toggled to "list it below instead":
            # the stage's own cleared count first (the bar itself shrinks to
            # just its own green sliver in that mode, so this is the only
            # place that value stays visible at full size), then every
            # breakdown reason that actually has candidates in it.
            display_chips = [{'label': label, 'count': value, 'flow': flow}] + breakdown_items

            stage.update(
                value=value,
                value_label=label,
                value_flow=flow,
                pct=pct,
                chips=display_chips,
                segments=segments,
            )
            prev_value = value

        ctx['upcoming_interviews'] = Interview.objects.select_related('candidate', 'interviewer').filter(
            status=Interview.Status.SCHEDULED, scheduled_date__gte=timezone.now()
        ).order_by('scheduled_date')[:10]

        # ---------- Daily View: actions HR took on a day, not current state ----------
        daily_range = daily_view.range_from_request(self.request.GET)
        ctx['daily_from'], ctx['daily_to'] = daily_range
        ctx['daily_days'] = (daily_range[1] - daily_range[0]).days + 1
        daily_results = daily_view.compute(daily_range, job_id or None)
        daily_max = max((c['value'] for c in daily_results), default=0) or 1
        daily_colors = ['#0e6f6b', '#c9d3d1', '#a9b6b3', '#8a9a9a', '#6b7a7a', '#4f5c5c']
        for i, c in enumerate(daily_results):
            c['bar_pct'] = round(c['value'] / daily_max * 100, 1)
            c['color'] = daily_colors[i % len(daily_colors)]
        ctx['daily_results'] = daily_results
        return ctx


class DailyActionDrilldownView(GroupRequiredMixin, TemplateView):
    """What a Daily View bar's click lands on: the actual events (not just
    the count) behind one column, for the same date range and job filter
    the chart was showing."""
    template_name = 'dashboard/daily_drilldown.html'
    allowed_groups = ANY_STAFF

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        column = kwargs['column']
        labels = dict(daily_view.COLUMNS)
        if column not in labels:
            raise Http404('Unknown Daily View column.')

        job_id = self.request.GET.get('job') or ''
        date_range = daily_view.range_from_request(self.request.GET)

        ctx['column'] = column
        ctx['column_label'] = labels[column]
        ctx['daily_from'], ctx['daily_to'] = date_range
        ctx['selected_job'] = job_id
        ctx['job'] = get_object_or_404(Job, pk=job_id) if job_id else None
        ctx['rows'] = daily_view.events(column, date_range, job_id or None)
        ctx['back_label'] = 'Back to Daily View'
        ctx['back_url'] = (f"{reverse('hr_dashboard')}?view=daily"
                           f"&daily_from={date_range[0]:%Y-%m-%d}&daily_to={date_range[1]:%Y-%m-%d}"
                           f"{f'&job={job_id}' if job_id else ''}")
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
        ctx['total_applicants'] = total
        ctx['rejected_count'] = rejected

        jobs_qs = Job.objects.filter(pk=job_id) if job_id else Job.objects.all()
        total_jobs = jobs_qs.count()
        jobs_with_hire = jobs_qs.filter(candidates__status=STATUS.HIRED).distinct().count()
        ctx['fill_rate'] = round(jobs_with_hire / total_jobs * 100, 1) if total_jobs else 0
        ctx['jobs_with_hire'] = jobs_with_hire
        ctx['total_jobs'] = total_jobs
        ctx['fill_dots'] = [True] * jobs_with_hire + [False] * (total_jobs - jobs_with_hire)

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

        # "Where applicants come from": one bar per source, scaled to the busiest source.
        max_total = max((r['total'] for r in data), default=0) or 1
        ctx['source_volume'] = [
            {**r,
             'bar_w': round(r['total'] / max_total * 100, 1),
             'share_pct': round(r['total'] / total * 100, 1) if total else 0}
            for r in data
        ]
        # Source effectiveness table: ranked by shortlist rate, not volume.
        ctx['source_effectiveness'] = sorted(data, key=lambda r: r['shortlist_pct'], reverse=True)
        return ctx
