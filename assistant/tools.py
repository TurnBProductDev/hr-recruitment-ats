"""Read-only data tools "Ask the ATS" can call (OpenAI function calling).

Every tool only reads, and only through the ORM with fixed, validated
filters - the model never writes SQL and nothing here changes data. Each
returns plain JSON-able data, capped in size, with in-app links (`url`) the
answer can point HR to. TOOL_SPECS is what the model sees; run_tool()
dispatches a call by name and never raises (errors come back as data).
"""
import json
import logging
from datetime import timedelta

from django.db.models import Avg, Count, Q
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date

from candidates import cv_text
from candidates.flows import flow_count
from candidates.models import Candidate
from candidates.templatetags.candidates_extras import score_band
from interviews import kpis as iv_kpis

from jobs.models import Job

logger = logging.getLogger(__name__)

S = Candidate.Status
GENERAL_APPLICATION = 'General Application'
STATUS_KEYS = [k for k, _ in S.choices]
STATUS_LABELS = dict(S.choices)
MAX_RESULT_CHARS = 14000


def _url(name, *args):
    return reverse(name, args=args)


def _cand_row(c):
    return {
        'id': c.pk, 'name': c.full_name, 'url': _url('candidate_timeline', c.pk),
        'vacancy': f'{c.job.job_code} - {c.job.title}' if c.job else None,
        'status': STATUS_LABELS.get(c.status, c.status),
        'score': c.match_score, 'band': score_band(c.match_score) or None,
        'source': c.source, 'applied': c.created_at.date().isoformat() if c.created_at else None,
        'experience_years': float(c.total_experience_years) if c.total_experience_years is not None else None,
        'last_role': ' at '.join(x for x in (c.last_role, c.last_company) if x) or None,
    }


def _job(job_code):
    if not job_code:
        return None
    return (Job.objects.filter(job_code__iexact=job_code.strip()).first()
            or Job.objects.filter(title__icontains=job_code.strip()).first())


def _filtered(args):
    """Candidate queryset for the shared filters (search/count tools)."""
    qs = Candidate.objects.select_related('job')
    if args.get('name'):
        for word in str(args['name']).split():
            qs = qs.filter(full_name__icontains=word)
    if args.get('job_code'):
        job = _job(args['job_code'])
        qs = qs.filter(job=job) if job else qs.none()
    if args.get('status'):
        statuses = [s.upper() for s in (args['status'] if isinstance(args['status'], list) else [args['status']])]
        qs = qs.filter(status__in=[s for s in statuses if s in STATUS_KEYS])
    if args.get('open_vacancies_only'):
        qs = qs.filter(job__status=Job.Status.OPEN, job__is_archived=False)
    if not args.get('include_general_applications'):
        qs = qs.exclude(job__title__iexact=GENERAL_APPLICATION)
    if args.get('min_score') is not None:
        qs = qs.filter(match_score__gte=int(args['min_score']))
    if args.get('max_score') is not None:
        qs = qs.filter(match_score__lte=int(args['max_score']))
    if args.get('source'):
        qs = qs.filter(source__icontains=args['source'])
    if args.get('applied_from') and parse_date(args['applied_from']):
        qs = qs.filter(created_at__date__gte=parse_date(args['applied_from']))
    if args.get('applied_to') and parse_date(args['applied_to']):
        qs = qs.filter(created_at__date__lte=parse_date(args['applied_to']))
    if args.get('min_experience_years') is not None:
        qs = qs.filter(total_experience_years__gte=args['min_experience_years'])
    if args.get('skill'):
        qs = qs.filter(Q(skills__icontains=args['skill']) | Q(cv_text__icontains=args['skill'])
                       | Q(cv_summary__icontains=args['skill']))
    return qs


# ----------------------------------------------------------------- tools

def search_candidates(args):
    qs = _filtered(args)
    sort = args.get('sort') or 'score'
    qs = qs.order_by('-match_score', '-created_at') if sort == 'score' else qs.order_by('-created_at')
    if sort == 'score':
        qs = qs.exclude(match_score__isnull=True) if args.get('scored_only') else qs
    limit = max(1, min(int(args.get('limit') or 15), 30))
    total = qs.count()
    return {'total_matching': total, 'showing': min(total, limit),
            'candidates': [_cand_row(c) for c in qs[:limit]]}


def get_candidate(args):
    c = (Candidate.objects.select_related('job').prefetch_related('education', 'experience_set')
         .filter(pk=args.get('candidate_id')).first())
    if c is None:
        return {'error': 'No candidate with that id - use search_candidates to find the id first.'}
    b = c.match_breakdown_parsed or {}
    history = [{'when': h.changed_at.date().isoformat(), 'from': STATUS_LABELS.get(h.old_status, h.old_status),
                'to': STATUS_LABELS.get(h.new_status, h.new_status), 'by': h.performed_by,
                'remarks': (h.remarks or '')[:300]}
               for h in c.history.filter(is_undone=False).order_by('changed_at')]
    calls = [{'when': log.logged_at.date().isoformat(), 'channel': log.get_channel_display(),
              'outcome': log.get_outcome_display() if log.outcome else None, 'note': (log.message or '')[:300]}
             for log in c.communication_logs.order_by('-logged_at')[:15]]
    notes = [{'when': n.created_at.date().isoformat(), 'text': (n.text or '')[:400]}
             for n in c.notes.order_by('-created_at')[:10]]
    interviews = [{'round': i.get_round_type_display(), 'date': timezone.localtime(i.scheduled_date).strftime('%d %b %Y %H:%M'),
                   'status': i.get_status_display(), 'result': i.get_result_display(),
                   'interviewer': (i.interviewer.get_full_name() or i.interviewer.get_username()) if i.interviewer else None,
                   'feedback': (i.feedback or '')[:400]}
                  for i in c.interviews.select_related('interviewer').order_by('scheduled_date')]
    full_cv = cv_text.get_cv_text(c)
    return {
        **_cand_row(c),
        'email': c.email, 'phone': c.phone, 'location': c.current_location,
        'notice_period': c.notice_period, 'current_salary': c.current_salary, 'expected_salary': c.expected_salary,
        'qualification': c.qualification, 'skills': c.skills, 'role_applied': c.role_applied,
        'education': [f'{e.qualification} - {e.institution or "-"} ({e.year_completed or "-"})'
                      for e in c.education.all()],
        'experience': [f'{e.designation or "-"} at {e.company_name} ({e.start_date or "?"} to {e.end_date or "present"})'
                       for e in c.experience_set.all()],
        'score_details': {
            'factors': b.get('factors'), 'candidate_overview': b.get('overview'),
            'key_strengths': b.get('strengths') or b.get('likes'),
            'key_concerns': b.get('concerns') or b.get('not_matched'),
            'rationale': c.match_rationale, 'scored_at': c.match_scored_at.date().isoformat() if c.match_scored_at else None,
        },
        'ai_cv_summary': (c.cv_summary or '')[:2500],
        'full_cv_text': (full_cv or '')[:7000] or 'Not available (CV not readable by the app).',
        'status_history': history, 'calls_and_emails': calls, 'notes': notes, 'interviews': interviews,
    }


def count_candidates(args):
    qs = _filtered(args)
    group_by = args.get('group_by')
    out = {'total': qs.count()}
    if group_by == 'status':
        out['by_status'] = {STATUS_LABELS.get(r['status'], r['status']): r['n']
                            for r in qs.values('status').annotate(n=Count('id')).order_by('-n')}
    elif group_by == 'vacancy':
        out['by_vacancy'] = [{'vacancy': f"{r['job__job_code']} - {r['job__title']}", 'count': r['n']}
                             for r in qs.values('job__job_code', 'job__title').annotate(n=Count('id')).order_by('-n')[:40]]
    elif group_by == 'source':
        out['by_source'] = {r['source'] or 'Unknown': r['n']
                            for r in qs.values('source').annotate(n=Count('id')).order_by('-n')}
    elif group_by == 'score_band':
        bands = {}
        for score in qs.values_list('match_score', flat=True):
            label = score_band(score) or 'Not scored'
            bands[label] = bands.get(label, 0) + 1
        out['by_score_band'] = bands
    elif group_by == 'applied_month':
        months = {}
        for d in qs.values_list('created_at', flat=True):
            if d:
                key = d.strftime('%Y-%m')
                months[key] = months.get(key, 0) + 1
        out['by_month'] = dict(sorted(months.items()))
    avg = qs.exclude(match_score__isnull=True).aggregate(a=Avg('match_score'))['a']
    out['average_score'] = round(avg, 1) if avg is not None else None
    return out


def pipeline_summary(args):
    base = Candidate.objects.exclude(job__title__iexact=GENERAL_APPLICATION).exclude(
        status=S.SCREENING_HOLD, hold_from_status=S.OPEN)
    job = _job(args.get('job_code'))
    if args.get('job_code') and not job:
        return {'error': f"No vacancy matching {args['job_code']!r}."}
    if job:
        base = base.filter(job=job)
    elif args.get('open_vacancies_only', True):
        base = base.filter(job__status=Job.Status.OPEN, job__is_archived=False)
    flows = ['open', 'ever_shortlisted', 'screened_out', 'call_pending', 'call_decision_pending',
             'shortlisted_after_call', 'rejected_after_call', 'hold_before_round1', 'r1_yet', 'r1_scheduled',
             'r1_no_show', 'r1_decision_pending', 'r1_cleared', 'rejected_after_round1', 'hold_before_round2',
             'r2_yet', 'r2_scheduled', 'r2_decision_pending', 'r2_cleared', 'rejected_after_round2',
             'hold_before_final', 'on_hold', 'hired', 'rejected_after_final']
    return {
        'scope': f'{job.job_code} - {job.title}' if job else ('open vacancies' if args.get('open_vacancies_only', True)
                                                              else 'all vacancies'),
        'total_candidates': base.count(),
        'current_status_counts': {STATUS_LABELS.get(r['status'], r['status']): r['n']
                                  for r in base.values('status').annotate(n=Count('id'))},
        'funnel_flows': {f: flow_count(base, f) for f in flows},
        'flow_meaning': ('open = screening pending; ever_shortlisted = cleared CV screening; screened_out = '
                         'rejected at CV screening; call_pending = yet to call; shortlisted_after_call = reached '
                         'Round 1; r1_cleared = cleared Round 1; r2_cleared = cleared Round 2; others are named by '
                         'stage (yet = yet to schedule, no_show = not turned up, decision_pending = interview done, '
                         'no result).'),
        'dashboard_url': _url('hr_dashboard') + '?view=overview',
    }


def list_interviews(args):
    bucket = args.get('bucket') or 'upcoming'
    if bucket not in iv_kpis.BUCKET_LABELS:
        return {'error': f'bucket must be one of {list(iv_kpis.BUCKET_LABELS)}'}
    qs = iv_kpis.active_interviews().filter(iv_kpis.bucket_q(bucket)).exclude(
        candidate__job__title__iexact=GENERAL_APPLICATION)
    job = _job(args.get('job_code'))
    if job:
        qs = qs.filter(candidate__job=job)
    qs = qs.select_related('candidate', 'candidate__job', 'interviewer').order_by('scheduled_date')
    limit = max(1, min(int(args.get('limit') or 20), 40))
    return {
        'bucket': iv_kpis.BUCKET_LABELS[bucket], 'total': qs.count(),
        'list_url': _url('interview_scheduler') + f'?bucket={bucket}' + (f'&job={job.pk}' if job else ''),
        'interviews': [{
            'candidate': i.candidate.full_name, 'candidate_url': _url('candidate_timeline', i.candidate_id),
            'vacancy': i.candidate.job.job_code if i.candidate.job else None, 'round': i.get_round_type_display(),
            'when': timezone.localtime(i.scheduled_date).strftime('%d %b %Y %H:%M'), 'status': i.get_status_display(),
            'interviewer': (i.interviewer.get_full_name() or i.interviewer.get_username()) if i.interviewer else 'Unassigned',
        } for i in qs[:limit]],
    }


def list_vacancies(args):
    qs = Job.objects.exclude(title__iexact=GENERAL_APPLICATION)
    if not args.get('include_closed'):
        qs = qs.filter(status=Job.Status.OPEN, is_archived=False)
    qs = qs.annotate(applicants=Count('candidates', distinct=True),
                     avg_score=Avg('candidates__match_score')).order_by('title')
    return {'vacancies': [{
        'job_code': j.job_code, 'title': j.title, 'location': j.location, 'status': j.get_status_display(),
        'archived': j.is_archived, 'openings': j.openings, 'applicants': j.applicants,
        'average_score': round(j.avg_score, 1) if j.avg_score is not None else None,
        'must_haves': j.must_have_list, 'opened': j.opening_date.isoformat() if j.opening_date else None,
        'description_excerpt': (j.description or '')[:600],
    } for j in qs[:40]], 'vacancies_url': _url('job_manage_list')}


def daily_activity(args):
    from dashboard import daily_view
    start = parse_date(args.get('from_date') or '') or (timezone.localdate() - timedelta(days=6))
    end = parse_date(args.get('to_date') or '') or timezone.localdate()
    if start > end:
        start, end = end, start
    job = _job(args.get('job_code'))
    results = daily_view.compute((start, end), job.pk if job else None)
    return {'from': start.isoformat(), 'to': end.isoformat(),
            'columns': [{'stage': r['label'], 'actions': r['value'], 'candidates': r['candidate_count'],
                         'breakdown_candidates': {b['label']: b['value'] for b in r['breakdown'] if b['value']}}
                        for r in results],
            'daily_view_url': _url('hr_dashboard') + f'?view=daily&daily_from={start}&daily_to={end}'}


# ------------------------------------------------------------ specs/dispatch

_FILTER_PROPS = {
    'name': {'type': 'string', 'description': 'Part of the candidate name.'},
    'job_code': {'type': 'string', 'description': 'Vacancy job code (e.g. SA12026) or part of its title.'},
    'status': {'type': 'array', 'items': {'type': 'string', 'enum': STATUS_KEYS},
               'description': 'Status codes. SHORTLISTED = Qualified (tele screening), INTERVIEW = Round 2, '
                              'SCREENING_HOLD = Hold.'},
    'open_vacancies_only': {'type': 'boolean'},
    'include_general_applications': {'type': 'boolean'},
    'min_score': {'type': 'integer'}, 'max_score': {'type': 'integer'},
    'source': {'type': 'string', 'description': 'e.g. Linked In, Careers, Naukri, Indeed, Referral, Agency.'},
    'applied_from': {'type': 'string', 'description': 'YYYY-MM-DD'},
    'applied_to': {'type': 'string', 'description': 'YYYY-MM-DD'},
    'min_experience_years': {'type': 'number'},
    'skill': {'type': 'string', 'description': 'A skill/keyword to look for in skills, CV text and summary.'},
}


def _fn(name, description, properties=None, required=None):
    return {'type': 'function', 'function': {
        'name': name, 'description': description,
        'parameters': {'type': 'object', 'properties': properties or {}, 'required': required or []}}}


TOOL_SPECS = [
    _fn('search_candidates', 'Find candidates by name and/or filters; returns ids, status, score, links. Use it '
        'first to get a candidate_id for get_candidate.',
        {**_FILTER_PROPS, 'sort': {'type': 'string', 'enum': ['score', 'recent']},
         'scored_only': {'type': 'boolean'}, 'limit': {'type': 'integer', 'description': 'max 30'}}),
    _fn('get_candidate', "One candidate's full file: profile, full CV text, CV score with band, factor ratings, "
        'Candidate Overview, Key Strengths, Key Concerns, status history with remarks, calls, notes and '
        'interviews.', {'candidate_id': {'type': 'integer'}}, ['candidate_id']),
    _fn('count_candidates', 'Count candidates matching filters, optionally grouped (by status, vacancy, source, '
        'score band or month applied); also gives the average score.',
        {**_FILTER_PROPS, 'group_by': {'type': 'string',
                                       'enum': ['status', 'vacancy', 'source', 'score_band', 'applied_month']}}),
    _fn('pipeline_summary', 'Hiring funnel numbers (as on the dashboard Overview) for one vacancy or all open '
        'vacancies.', {'job_code': {'type': 'string'}, 'open_vacancies_only': {'type': 'boolean'}}),
    _fn('list_interviews', 'Interviews in a bucket: today, upcoming (all future), result_pending (done, no '
        'result), overdue (time passed, not marked done).',
        {'bucket': {'type': 'string', 'enum': list(iv_kpis.BUCKET_LABELS)}, 'job_code': {'type': 'string'},
         'limit': {'type': 'integer'}}, ['bucket']),
    _fn('list_vacancies', 'Vacancies with openings, applicant counts, average score and must-haves.',
        {'include_closed': {'type': 'boolean'}}),
    _fn('daily_activity', 'Actions HR took per stage in a date range (Daily View), e.g. "what did we do this '
        'week".', {'from_date': {'type': 'string', 'description': 'YYYY-MM-DD'},
                   'to_date': {'type': 'string', 'description': 'YYYY-MM-DD'}, 'job_code': {'type': 'string'}}),
]

_TOOLS = {
    'search_candidates': search_candidates, 'get_candidate': get_candidate, 'count_candidates': count_candidates,
    'pipeline_summary': pipeline_summary, 'list_interviews': list_interviews, 'list_vacancies': list_vacancies,
    'daily_activity': daily_activity,
}


def run_tool(name, arguments_json):
    """JSON string result for one tool call. Never raises."""
    fn = _TOOLS.get(name)
    if fn is None:
        return json.dumps({'error': f'Unknown tool {name}'})
    try:
        args = json.loads(arguments_json or '{}')
        if not isinstance(args, dict):
            args = {}
        result = fn(args)
    except Exception as exc:  # noqa: BLE001 - a bad call must come back as data, not a 500
        logger.exception('Ask the ATS tool %s failed', name)
        result = {'error': f'{type(exc).__name__}: {exc}'}
    text = json.dumps(result, default=str)
    if len(text) > MAX_RESULT_CHARS:
        text = text[:MAX_RESULT_CHARS] + '... [truncated - narrow the filters]'
    return text
