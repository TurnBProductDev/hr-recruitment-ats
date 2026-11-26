"""Checks run once on every new application: auto-reject applications from
blacklisted emails and repeat applications for the same vacancy within 6
months, then auto-score whatever is left in Open on a real vacancy
(candidates/scoring.auto_score_eligible).

Blacklist rule (checked first, no time limit, any vacancy incl. General
Application): if this email was ever blacklisted (services.email_blacklisted
- survives the blacklisted record being deleted), the new application is
rejected straight away with reason "Blacklisted Earlier", Done by "Blacklist
Check", and is locked there (services.locked_by_blacklist) - it can't be
reopened, moved on, or undone back to Open.

When the same email applies again for the same vacancy within 6 months of an
earlier application, the earlier application decides what happens:

  earlier is ...                         -> action
  Hired                                  -> nothing
  in process (Qualified, Round 1/2,      -> the NEW application is rejected as
    Final Selection, or on Hold)            a duplicate; the original is kept
  Rejected / Blacklisted                 -> the NEW application is rejected
                                            (6-month cool-off)
  still Open (not yet screened)          -> the EARLIER one is rejected and
                                            the new one is kept

No email goes to the candidate; the reason is written to status history
(Done by "Duplicate Check"). General Application and CVs with no readable
email are never touched.

Every intake route is covered by checking each candidate once, in id order,
from a high-water mark (DuplicateRuleState) rather than hooking each route:
the careers form and Bulk Upload call run_pending() straight after creating
the candidate, and DuplicateRuleMiddleware runs it before any signed-in page,
which picks up careers-mailbox applications the stored procedure inserted
directly. The mark starts at the newest candidate when this was deployed, so
existing candidates are never changed.
"""
import calendar
import logging

from django.core.cache import cache
from django.db import transaction
from django.db.models import Max

from .models import PLACEHOLDER_EMAIL_DOMAIN, Candidate, DuplicateRuleState

logger = logging.getLogger(__name__)

STATUS = Candidate.Status
WINDOW_MONTHS = 6
PERFORMED_BY = 'Duplicate Check'
BATCH = 200

IN_PROCESS = {STATUS.SHORTLISTED, STATUS.ROUND1, STATUS.INTERVIEW, STATUS.FINAL_SELECTION,
              STATUS.SCREENING_HOLD}
ENDED = {STATUS.REJECTED, STATUS.BLACKLISTED}


def months_before(dt, months):
    """Same day-of-month `months` calendar months earlier (clamped to the
    month's last day, e.g. 31 Aug -> 28/29 Feb)."""
    month_index = dt.year * 12 + (dt.month - 1) - months
    year, month = divmod(month_index, 12)
    month += 1
    day = min(dt.day, calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


BLACKLIST_PERFORMED_BY = 'Blacklist Check'


def _reject(candidate, remarks):
    from .services import change_status  # local: services imports models, keep this importable
    from .reject_reasons import DUPLICATE
    change_status(candidate, STATUS.REJECTED, remarks=remarks, performed_by=PERFORMED_BY, reject_reason=DUPLICATE)


def _apply_blacklist(candidate):
    """Reject a new application whose email was blacklisted before. Also
    catches one the careers-mailbox stored procedure inserted already marked
    Blacklisted - but never the record HR blacklisted themselves."""
    from . import services
    from .reject_reasons import BLACKLISTED_EARLIER
    if candidate.status not in (STATUS.OPEN, STATUS.BLACKLISTED) or not candidate.email:
        return None
    if candidate.email.lower().endswith('@' + PLACEHOLDER_EMAIL_DOMAIN):
        return None
    if candidate.blacklist_entries.exists() or not services.email_blacklisted(candidate.email):
        return None
    if not candidate.is_blacklisted:
        candidate.is_blacklisted = True
        candidate.save(update_fields=['is_blacklisted'])
    services.change_status(
        candidate, STATUS.REJECTED, performed_by=BLACKLIST_PERFORMED_BY, reject_reason=BLACKLISTED_EARLIER,
        remarks='Auto-rejected: this email address was blacklisted on an earlier application.')
    return 'rejected (email blacklisted earlier)'


def apply_to(candidate):
    """Apply the rules to one newly arrived candidate. Returns a short
    description of what was done, or None."""
    from .views import GENERAL_APPLICATION  # local: avoids an import cycle
    result = _apply_blacklist(candidate)
    if result:
        return result
    if candidate.status != STATUS.OPEN or not candidate.job_id or not candidate.email:
        return None
    if candidate.email.lower().endswith('@' + PLACEHOLDER_EMAIL_DOMAIN):
        return None
    if (candidate.job.title or '').strip().lower() == GENERAL_APPLICATION.lower():
        return None

    earlier = list(
        Candidate.objects.filter(
            email__iexact=candidate.email, job_id=candidate.job_id,
            created_at__gte=months_before(candidate.created_at, WINDOW_MONTHS),
            created_at__lte=candidate.created_at,
        ).exclude(pk=candidate.pk).order_by('-created_at'))
    if not earlier:
        return None
    statuses = {c.status for c in earlier}
    if STATUS.HIRED in statuses:
        return None

    in_process = [c for c in earlier if c.status in IN_PROCESS]
    if in_process:
        _reject(candidate, f'Auto-rejected: duplicate application - {in_process[0].candidate_code} '
                           f'for the same vacancy is already in process.')
        return 'rejected new (duplicate of an application in process)'

    ended = [c for c in earlier if c.status in ENDED]
    if ended:
        _reject(candidate, f'Auto-rejected: applied again for the same vacancy within {WINDOW_MONTHS} '
                           f'months of an earlier application that was rejected '
                           f'({ended[0].candidate_code}, applied {ended[0].created_at:%d %b %Y}).')
        return 'rejected new (earlier application was rejected)'

    for old in (c for c in earlier if c.status == STATUS.OPEN):
        _reject(old, f'Auto-rejected: superseded by a newer application for the same vacancy '
                     f'({candidate.candidate_code}).')
    return 'rejected earlier open application(s), kept new'


def run_pending():
    """Check every candidate created since the last run, oldest first.
    Safe to call from several workers at once - the state row is locked."""
    done = 0
    with transaction.atomic():
        state = DuplicateRuleState.objects.select_for_update().order_by('pk').first()
        if state is None:
            # First ever run (normally the migration seeds this): start from
            # the newest existing candidate, so nothing already there changes.
            newest = Candidate.objects.aggregate(m=Max('pk'))['m'] or 0
            DuplicateRuleState.objects.create(last_candidate_id=newest)
            return 0
        pending = list(Candidate.objects.select_related('job')
                       .filter(pk__gt=state.last_candidate_id).order_by('pk')[:BATCH])
        for candidate in pending:
            candidate.refresh_from_db()  # an earlier one in this batch may have changed it
            try:
                with transaction.atomic():
                    result = apply_to(candidate)
                if result:
                    logger.info('Duplicate rule: candidate %s - %s', candidate.pk, result)
            except Exception:  # noqa: BLE001 - one bad row must not block every later one
                logger.exception('Duplicate rule failed for candidate %s', candidate.pk)
            state.last_candidate_id = candidate.pk
            done += 1
        if pending:
            state.save(update_fields=['last_candidate_id'])
            _queue_auto_scoring([c.pk for c in pending])
    return done


def _queue_auto_scoring(pks):
    """Score the new applications still worth scoring - decided only after the
    whole batch, since a later one can reject an earlier one - once this
    transaction has committed (so the scoring thread sees the final rows)."""
    from . import scoring
    try:
        eligible = [c.pk for c in Candidate.objects.select_related('job').filter(pk__in=pks).order_by('pk')
                    if scoring.auto_score_eligible(c)]
    except Exception:  # noqa: BLE001 - scoring must never block intake
        logger.exception('Could not pick new applications to auto-score')
        return
    if eligible:
        transaction.on_commit(lambda: scoring.start_scoring(eligible))


class DuplicateRuleMiddleware:
    """Runs run_pending() before signed-in pages, so careers-mailbox
    applications (inserted straight into the database) are checked before
    HR sees them. Throttled per worker to keep it to ~one cheap query a
    few times a minute."""
    THROTTLE_SECONDS = 15
    CACHE_KEY = 'duplicate_rule_last_run'

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, 'user', None)
        if user is not None and user.is_authenticated and cache.add(self.CACHE_KEY, 1, self.THROTTLE_SECONDS):
            try:
                run_pending()
            except Exception:  # noqa: BLE001 - never break a page over this
                logger.exception('Duplicate rule sweep failed')
        return self.get_response(request)
