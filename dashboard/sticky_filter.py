"""One Vacancy / "Open vacancies only" filter shared by every filtered page.

Picking a vacancy on the Dashboard, Reports, Candidate Repository or
Scoring Criteria page remembers it, and opening any of those pages - or the
Interviews list - without a vacancy in its link brings the same filter back,
until HR changes it, clears it ("All vacancies"), signs in/out, or closes the
browser.

How it works:
- The filter forms carry a hidden `setfilter=1`. Only a submission with it
  saves the filter, so an ordinary drill-down link (e.g. a By Vacancy cell
  with its own `job=`) is followed as-is without changing what's remembered.
- The filter lives in a browser-session cookie (no expiry), so it ends when
  the browser closes without touching the login session itself.
- A filtered page opened without `job` / scope in its query string is
  redirected to the same URL with the remembered values added. Every view
  keeps reading the plain `job` / `scoped` / `scope` GET params exactly as
  before, and the address bar always shows what's applied.
- The default (all vacancies, open only) is never stored, so nothing changes
  for anyone who hasn't picked a filter.
"""
from urllib.parse import parse_qs, urlencode

from django.contrib.auth.signals import user_logged_in, user_logged_out
from django.dispatch import receiver
from django.http import HttpResponseRedirect
from django.urls import Resolver404, resolve

COOKIE = 'ats_filter'

# url_name -> whether that page understands the scope switch at all. Pages
# without it (Reports, Scoring Criteria) still get the vacancy carried over.
FILTERED_PAGES = {
    'hr_dashboard': True,
    'candidate_repository': True,
    'interview_scheduler': True,
    'hr_reports': False,
    'scoring_criteria': False,
}

DEFAULT = {'job': '', 'scope': 'open'}


def _read(request):
    raw = request.COOKIES.get(COOKIE)
    if not raw:
        return None
    parsed = parse_qs(raw, keep_blank_values=True)
    job = (parsed.get('job') or [''])[0]
    scope = (parsed.get('scope') or ['open'])[0]
    if not (job == '' or job.isdigit()) or scope not in ('', 'open'):
        return None
    return {'job': job, 'scope': scope}


def saved_filter(request):
    """The remembered filter for this browser, or None (= the default)."""
    if hasattr(request, '_sticky_filter'):
        return request._sticky_filter
    return _read(request)


def _is_open_job(job_id):
    from jobs.models import Job  # local: keeps this module importable at settings load
    return Job.objects.filter(pk=job_id, status=Job.Status.OPEN, is_archived=False).exists()


@receiver(user_logged_in)
@receiver(user_logged_out)
def _forget_on_sign_in_or_out(sender, request=None, **kwargs):
    # A different person signing in on the same browser starts clean.
    if request is not None:
        request._sticky_clear = True


class StickyFilterMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        action = None  # None | ('set', dict) | 'clear'
        saved = _read(request)
        page = self._page(request)

        if page is not None:
            get = request.GET
            if get.get('setfilter') == '1':
                job = get.get('job', '').strip()
                if not (job == '' or job.isdigit()):
                    job = ''
                if 'scoped' in get:
                    scope = 'open' if get.get('scope') == 'open' else ''
                else:  # a form without the switch leaves the scope as it was...
                    scope = (saved or DEFAULT)['scope']
                    # ...unless the vacancy picked (Reports lists closed ones
                    # too) is closed - "open only" would then hide it everywhere.
                    if job and scope == 'open' and not _is_open_job(job):
                        scope = ''
                saved = {'job': job, 'scope': scope}
                action = 'clear' if saved == DEFAULT else ('set', saved)
                if saved == DEFAULT:
                    saved = None
            elif saved:
                redirect_to = self._fill_in(request, saved, has_scope=FILTERED_PAGES[page])
                if redirect_to:
                    return HttpResponseRedirect(redirect_to)

        request._sticky_filter = saved
        response = self.get_response(request)

        if getattr(request, '_sticky_clear', False):
            action = 'clear'
        if action == 'clear':
            if COOKIE in request.COOKIES:
                response.delete_cookie(COOKIE, samesite='Lax')
        elif action:
            response.set_cookie(COOKIE, urlencode(action[1]), max_age=None, httponly=True,
                                samesite='Lax', secure=request.is_secure())
        return response

    @staticmethod
    def _page(request):
        if request.method != 'GET' or not getattr(request, 'user', None) or not request.user.is_authenticated:
            return None
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return None
        try:
            name = resolve(request.path_info).url_name
        except Resolver404:
            return None
        return name if name in FILTERED_PAGES else None

    @staticmethod
    def _fill_in(request, saved, has_scope):
        """URL with the remembered values added where the request has none,
        or None if nothing is missing."""
        get = request.GET.copy()
        changed = False
        if 'job' not in get and saved['job']:
            get['job'] = saved['job']
            changed = True
        if has_scope and 'scoped' not in get and 'scope' not in get and saved['scope'] != 'open':
            # Only "open vacancies only" switched OFF needs carrying - every
            # page already defaults to it ON.
            get['scoped'] = '1'
            changed = True
        elif has_scope and changed and 'scoped' not in get and 'scope' not in get:
            get['scoped'] = '1'
            get['scope'] = 'open'
        if not changed:
            return None
        return f'{request.path}?{get.urlencode()}'
