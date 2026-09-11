from django.db.models import Count, Max, Q, Sum
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.views.generic import TemplateView

from candidates.flows import flow_count
from candidates.models import Candidate
from candidates.permissions import ANY_STAFF, GroupRequiredMixin
from candidates.views import GENERAL_APPLICATION
from interviews.models import Interview
from jobs.models import Job

from . import daily_view

STATUS = Candidate.Status


# The By Job/Role and By Source tables (and the Summary KPI cards) roll the
# nine statuses into four buckets so the columns always add up to Total.
# Every status belongs to exactly one bucket - add any new status here or the
# row stops balancing (see dashboard.tests.StatusBucketTests).
#
# A hold taken before the candidate was ever screened (INITIAL_HOLD) counts as
# Rejected rather than Open/Unattended - those candidates are tracked instead
# on the dedicated Future Prospects page (candidates.views.FutureProspectsListView)
# as a pool to revisit for future openings, not as an active application. A
# hold taken at any later stage counts as Active Pool (Shortlisted), not
# Open/Unattended - by definition some action was already taken to get them
# there (shortlisted, interviewed, ...) before they were held.
INITIAL_HOLD = Q(status=STATUS.SCREENING_HOLD, hold_from_status=STATUS.OPEN)
OPEN_GROUP = Q(status=STATUS.OPEN)
SHORTLISTED_GROUP = Q(status__in=(STATUS.SHORTLISTED, STATUS.ROUND1, STATUS.INTERVIEW,
                                  STATUS.FINAL_SELECTION)) | (Q(status=STATUS.SCREENING_HOLD) & ~INITIAL_HOLD)
REJECTED_GROUP = Q(status__in=(STATUS.REJECTED, STATUS.BLACKLISTED)) | INITIAL_HOLD
HIRED_GROUP = Q(status=STATUS.HIRED)


def _grouped_counts():
    """Count kwargs shared by the summary panel and the two breakdown tables."""
    return dict(
        total=Count('id'),
        open=Count('id', filter=OPEN_GROUP),
        shortlisted=Count('id', filter=SHORTLISTED_GROUP),
        rejected=Count('id', filter=REJECTED_GROUP),
        hired=Count('id', filter=HIRED_GROUP),
    )


def _summary_counts_qs(qs):
    """Total / Open / Shortlisted / Rejected / Hired for a candidate queryset (one query)."""
    return qs.aggregate(**_grouped_counts())


GREEN = '#0e6f6b'
# Funnel bar segment colours, keyed by category rather than by rank/size, so
# the same reason always reads the same colour wherever it shows up in the
# funnel. (bg, text) - bg is a light/transparent tint, text is the solid
# colour used for the tooltip figure and the inline label when there's room.
SEG_COLORS = {
    'red': ('rgba(220,53,69,.14)', '#b02a37'),      # Rejected / Future Prospects
    'yellow': ('rgba(255,193,7,.22)', '#8a6d00'),    # Yet to Call / Yet to Schedule
    'purple': ('rgba(111,66,193,.15)', '#5a3d99'),   # Hold
    'green': ('rgba(40,167,69,.16)', '#1e7e34'),     # Scheduled
    'orange': ('rgba(253,126,20,.18)', '#a35300'),   # Not Turned Up
    'grey': ('rgba(31,42,42,.12)', '#33393c'),       # Decision Pending
}


class HRDashboardView(GroupRequiredMixin, TemplateView):
    template_name = 'dashboard/dashboard.html'
    allowed_groups = ANY_STAFF

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        job_id = self.request.GET.get('job') or ''
        # 'scoped' only appears once the filter form has actually been
        # submitted (it's a hidden field on it) - an unchecked checkbox isn't
        # sent at all, so that's indistinguishable from a fresh visit with no
        # query string otherwise. Every fresh arrival at the dashboard (nav
        # link, a drill-down back-link, ...) defaults to Open vacancies only;
        # explicitly unchecking it during this session is still respected for
        # that request.
        if 'scoped' in self.request.GET:
            scope = self.request.GET.get('scope', '')
        else:
            scope = 'open'
        base = Candidate.objects.all()
        if job_id:
            base = base.filter(job_id=job_id)
        if scope == 'open':   # only candidates under currently-open vacancies
            base = base.filter(job__status=Job.Status.OPEN, job__is_archived=False)
        # General Application candidates aren't mapped to a role, and Future
        # Prospects (a hold taken before ever being screened) is tracked as
        # its own pool to revisit later - see candidates.views.GENERAL_APPLICATION
        # and FutureProspectsListView. Neither counts as an active application,
        # so both are left out of every dashboard number; their own counts are
        # shown separately (ctx['general_applications_count']/['future_prospects_count'])
        # instead of folded into Rejected/Total the way Future Prospects used to be.
        base = base.exclude(job__title__iexact=GENERAL_APPLICATION).exclude(INITIAL_HOLD)

        job_list = Job.objects.all().order_by('title')
        if scope == 'open':
            job_list = job_list.filter(status=Job.Status.OPEN, is_archived=False)
        ctx['jobs'] = job_list
        ctx['selected_job'] = job_id
        ctx['scope'] = scope
        ctx['view'] = self.request.GET.get('view', 'summary')
        ctx['general_applications_count'] = Candidate.objects.filter(
            job__title__iexact=GENERAL_APPLICATION).count()
        ctx['future_prospects_count'] = Candidate.objects.filter(INITIAL_HOLD).count()

        # ---------- Summary ----------
        # Open vacancies, not candidates - always the global count regardless of
        # the job/candidate filters above, so picking one role doesn't make it look
        # like there's only one opening. Sum of Job.openings (individual positions
        # to fill), not a count of vacancy postings - one posting can be for more
        # than one opening (see jobs/models.py). General Application is never a
        # real vacancy, so it's excluded even if its status were ever OPEN.
        ctx['open_positions'] = Job.objects.filter(
            status=Job.Status.OPEN, is_archived=False
        ).exclude(title__iexact=GENERAL_APPLICATION).aggregate(n=Sum('openings'))['n'] or 0
        ctx['summary'] = _summary_counts_qs(base)
        ctx['by_job'] = (base.values('job__title')
                         # Max, not Sum: `openings` is a per-job value, not per-candidate -
                         # summing it across every candidate row in the group would multiply
                         # it out by however many candidates that job has.
                         .annotate(openings=Max('job__openings'), **_grouped_counts())
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
        screened_out = fc('screened_out')
        s_after_call = fc('shortlisted_after_call')
        # 'Unable to Connect' is folded into 'Yet to Call' below - call_pending's
        # flow filter already includes it (see flows.py) - rather than shown as
        # its own segment.
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
        # Hold chip and the "cleared" stage they'd already passed. (The
        # before-screening carve-out is folded into 'screened_out' above,
        # not listed here - it's Rejected, not Hold, from this stage on.)
        hold_before_round1 = fc('hold_before_round1')
        hold_before_round2 = fc('hold_before_round2')
        hold_before_final = fc('hold_before_final')
        hold_after_final = fc('hold_after_final')

        screening_pending = fc('open')
        ctx['funnel_top'] = {
            'total': base.count(), 'requirement': requirement,
            # Shown as its own card above the bar chart, not as a funnel
            # stage - these candidates haven't reached a screening decision
            # yet, so they're excluded from the bars entirely (see peak below).
            'screening_pending': screening_pending,
        }
        # Candidates held before ever reaching screening don't get a card or
        # bar segment of their own any more - they're tracked on the Future
        # Prospects page instead (candidates.views.FutureProspectsListView) -
        # and are folded into the single Rejected count/segment here, same as
        # an outright screening rejection (see flows.screened_out).
        # Corner "total" = everyone who got a decision at that stage (cleared + rejected-there)
        ctx['funnel'] = [
            {'name': 'CV Screening',
             # No 'pending' entry here (see the funnel-building loop below) -
             # Screening Pending is the "screening_pending" card above, not a
             # bar segment or a breakdown chip, since it's not a screening
             # *decision* yet.
             'pending': None,
             'cleared': ('Qualified', shortlisted, shortlisted + screened_out, 'ever_shortlisted'),
             'drops': [('Rejected', screened_out, 'screened_out', 'red')]},
            {'name': 'Tele Screening',
             'pending': ('Yet to Call', yet_call, 'call_pending', 'yellow'),
             'cleared': ('Shortlisted', s_after_call, s_after_call + rej_call, 'shortlisted_after_call'),
             'drops': [('Decision Pending', call_dp, 'call_decision_pending', 'grey'),
                       ('Hold', hold_before_round1, 'hold_before_round1', 'purple'),
                       ('Rejected', rej_call, 'rejected_after_call', 'red')]},
            {'name': 'Round 1',
             'pending': ('Yet to Schedule', r1_yet, 'r1_yet', 'yellow'),
             'cleared': ('Cleared', r1_cleared, r1_cleared + rej_r1, 'r1_cleared'),
             'drops': [('Scheduled', r1_sched, 'r1_scheduled', 'green'),
                       ('Not Turned Up', r1_ns, 'r1_no_show', 'orange'),
                       ('Decision Pending', r1_dp, 'r1_decision_pending', 'grey'),
                       ('Hold', hold_before_round2, 'hold_before_round2', 'purple'),
                       ('Rejected', rej_r1, 'rejected_after_round1', 'red')]},
            {'name': 'Round 2',
             'pending': ('Yet to Schedule', r2_yet, 'r2_yet', 'yellow'),
             'cleared': ('Cleared', r2_cleared, r2_cleared + rej_r2, 'r2_cleared'),
             'drops': [('Scheduled', r2_sched, 'r2_scheduled', 'green'),
                       ('Not Turned Up', r2_ns, 'r2_no_show', 'orange'),
                       ('Decision Pending', r2_dp, 'r2_decision_pending', 'grey'),
                       ('Hold', hold_before_final, 'hold_before_final', 'purple'),
                       ('Rejected', rej_r2, 'rejected_after_round2', 'red')]},
            {'name': 'Hire',
             'pending': ('On Hold', on_hold, 'on_hold', 'purple'),
             'cleared': ('Hired', hired, hired + rej_r2 + rej_final, 'hired'),
             'drops': [('Hold', hold_after_final, 'hold_after_final', 'purple'),
                       ('Rejected', rej_final, 'rejected_after_final', 'red')]},
        ]

        # One bar per stage, breakdown baked in as shaded segments: a solid
        # green segment for the stage's own "cleared" count, then one segment
        # per breakdown reason coloured by category (not by rank/size) - so
        # the same reason always reads the same colour wherever it appears -
        # normalised so they always fill the bar out to exactly the previous
        # stage's share of peak, however their raw counts add up. The green
        # segment always shows its value at a legible size; other segments
        # shrink their font as they narrow instead of just disappearing, so
        # green is always the first thing a glance lands on.
        peak = (shortlisted + screened_out) or 1
        prev_value = shortlisted + screened_out
        for stage in ctx['funnel']:
            label, value, _decided, flow = stage['cleared']
            stage_total = prev_value  # everyone who reached this stage, cleared or not
            breakdown_chips = []
            if stage['pending']:
                p = stage['pending']
                breakdown_chips.append({'label': p[0], 'count': p[1], 'flow': p[2], 'cat': p[3]})
            breakdown_chips += [{'label': d[0], 'count': d[1], 'flow': d[2], 'cat': d[3]} for d in stage['drops']]
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
                font_size = '.78rem' if pct >= 6 else ('.68rem' if pct >= 3 else '.6rem')
                segments.append({
                    'flow': flow, 'value': value, 'label': label, 'pct': pct,
                    'bg': GREEN, 'text_color': '#fff', 'show_inline': True,
                    'font_size': font_size, 'is_green': True,
                })
            breakdown_items = sorted((c for c in breakdown_chips if c['count'] > 0), key=lambda c: -c['count'])
            breakdown_pct = round((prev_value - value) / peak * 100, 1)
            items_total = sum(c['count'] for c in breakdown_items) or 1
            for c in breakdown_items:
                seg_pct = round(c['count'] / items_total * breakdown_pct, 1)
                if seg_pct < 0.3:
                    seg_pct = 0.3
                bg, text_color = SEG_COLORS[c['cat']]
                font_size = '.68rem' if seg_pct >= 6 else ('.6rem' if seg_pct >= 3 else '.54rem')
                segments.append({
                    'flow': c['flow'], 'value': c['count'], 'label': c['label'], 'pct': seg_pct,
                    'bg': bg, 'text_color': text_color, 'show_inline': seg_pct >= 1.5,
                    'font_size': font_size, 'is_green': False,
                })

            # Chips shown when the row is toggled to "list it below instead":
            # the stage's own cleared count first (the bar itself shrinks to
            # just its own green sliver in that mode, so this is the only
            # place that value stays visible at full size), then every
            # breakdown reason that actually has candidates in it.
            display_chips = [{'label': label, 'count': value, 'flow': flow, 'accent': GREEN}] + [
                {'label': c['label'], 'count': c['count'], 'flow': c['flow'], 'accent': SEG_COLORS[c['cat']][1]}
                for c in breakdown_items
            ]

            stage.update(
                value=value,
                total=stage_total,
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
        ctx['breadcrumb_current'] = labels[column]
        ctx['daily_from'], ctx['daily_to'] = date_range
        ctx['selected_job'] = job_id
        ctx['job'] = get_object_or_404(Job, pk=job_id) if job_id else None
        ctx['rows'] = daily_view.events(column, date_range, job_id or None)
        ctx['back_label'] = 'Back to Daily View'
        ctx['back_url'] = (f"{reverse('hr_dashboard')}?view=daily"
                           f"&daily_from={date_range[0]:%Y-%m-%d}&daily_to={date_range[1]:%Y-%m-%d}"
                           f"{f'&job={job_id}' if job_id else ''}")
        return ctx


def _report_metrics(qs):
    """Applicants / Shortlisting Ratio / Round 1 Clear Ratio / Hired for one
    queryset - shared by the page-level KPIs and each By Job/Role and Source
    Level row, so every number on the Reports page uses the exact same
    definitions as the Dashboard Overview funnel (candidates.flows) instead
    of a second, parallel definition of "shortlisted"/"cleared Round 1" that
    could drift out of sync with it.

    Round 1 Clear Ratio's base is candidates who reached Round 1 at all
    ('shortlisted_after_call' - the funnel's own name for that transition),
    not total applicants - same as how the funnel bar computes each stage's
    own clear rate. None (not 0) when nobody reached Round 1, so the
    template can show "-" instead of a misleading 0.0%."""
    applicants = qs.count()
    shortlisted = flow_count(qs, 'ever_shortlisted')
    reached_r1 = flow_count(qs, 'shortlisted_after_call')
    r1_cleared = flow_count(qs, 'r1_cleared')
    hired = flow_count(qs, 'hired')
    return {
        'applicants': applicants,
        'shortlisted': shortlisted,
        'shortlisting_ratio': round(shortlisted / applicants * 100, 1) if applicants else 0,
        'reached_r1': reached_r1,
        'r1_cleared': r1_cleared,
        'r1_clear_ratio': round(r1_cleared / reached_r1 * 100, 1) if reached_r1 else None,
        'hired': hired,
        'hiring_ratio': round(hired / applicants * 100, 1) if applicants else 0,
    }


class ReportsView(GroupRequiredMixin, TemplateView):
    template_name = 'dashboard/reports.html'
    allowed_groups = ANY_STAFF

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        job_id = self.request.GET.get('job') or ''
        # General Application isn't a real vacancy and Future Prospects isn't
        # an active application - excluded from every Reports number, same
        # as the Dashboard Summary page (see HRDashboardView).
        base = Candidate.objects.exclude(job__title__iexact=GENERAL_APPLICATION).exclude(INITIAL_HOLD)
        if job_id:
            base = base.filter(job_id=job_id)

        date_from = self.request.GET.get('date_from', '')
        if date_from:
            base = base.filter(created_at__date__gte=date_from)
        date_to = self.request.GET.get('date_to', '')
        if date_to:
            base = base.filter(created_at__date__lte=date_to)

        ctx['jobs'] = Job.objects.exclude(title__iexact=GENERAL_APPLICATION).order_by('title')
        ctx['selected_job'] = job_id
        ctx['date_from'] = date_from
        ctx['date_to'] = date_to

        ctx.update(_report_metrics(base))

        # order_by() clears Candidate's default ordering (Meta.ordering =
        # ['-created_at']) before distinct() - otherwise Django folds
        # created_at into the SELECT DISTINCT (to satisfy that ordering),
        # so no two candidate rows are ever "distinct" and every title
        # comes back once per candidate instead of once per role.
        by_job_titles = (base.order_by().values_list('job__title', flat=True).distinct())
        ctx['by_job'] = sorted(
            ({'name': title or 'Unassigned', **_report_metrics(base.filter(job__title=title))}
             for title in by_job_titles),
            key=lambda r: -r['applicants'])

        by_source_names = (base.exclude(source__isnull=True).exclude(source='')
                           .order_by().values_list('source', flat=True).distinct())
        ctx['by_source'] = sorted(
            ({'name': source, **_report_metrics(base.filter(source=source))}
             for source in by_source_names),
            key=lambda r: -r['applicants'])
        return ctx
