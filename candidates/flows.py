"""Shared stage-flow filters for the funnel dashboard and the candidate list.

A 'flow' names a derived cohort (e.g. 'r1_cleared'). Using one definition in
both places guarantees the dashboard count and the drill-down list always match.

"Reached a stage" is read from the status history, so a candidate counts as
having cleared a stage even if they were later rejected/hired (funnel view).
"""
from django.db.models import OuterRef, Q, Subquery
from django.utils import timezone

from candidates.models import Candidate, CommunicationLog
from interviews.models import Interview

S = Candidate.Status
OUT = CommunicationLog.Outcome
IV = Interview
R1 = IV.RoundType.ROUND1
PASS = IV.Result.PASS_
SCHED = IV.Status.SCHEDULED
RESCHED = IV.Status.RESCHEDULED
CANC = IV.Status.CANCELLED
NON_R1 = [t for t, _ in IV.RoundType.choices if t != R1]
TERMINAL = [S.REJECTED, S.BLACKLISTED]


COMPLETED = IV.Status.COMPLETED
PENDING = IV.Result.PENDING


def _reached(stage):
    return Q(history__new_status=stage)


def _decision_pending(round_types):
    """Interviews of `round_types` whose outcome was never recorded: either marked
    completed with no pass/fail, or the date has passed and it still sits as
    scheduled. These are the 'we forgot to mark an action' cases."""
    rt = {'interviews__round_type__in': round_types}
    return (Q(**rt, interviews__status=COMPLETED, interviews__result=PENDING)
            | Q(**rt, interviews__status__in=[SCHED, RESCHED], interviews__result=PENDING,
                interviews__scheduled_date__lt=timezone.now()))


def _upcoming(round_types):
    """Still-to-happen interviews (scheduled and not yet past)."""
    return Q(interviews__round_type__in=round_types, interviews__status__in=[SCHED, RESCHED],
             interviews__scheduled_date__gte=timezone.now())


def _with_latest_call_outcome(qs):
    """Annotate with each candidate's most recently logged outcome, so a
    candidate with several calls over time lands in exactly one call-stage
    bucket (the one matching their latest attempt) instead of every bucket
    any of their calls ever matched."""
    latest = (CommunicationLog.objects.filter(candidate=OuterRef('pk'))
              .order_by('-logged_at').values('outcome')[:1])
    return qs.annotate(latest_call_outcome=Subquery(latest))


def flow_filter(qs, flow):
    """Return `qs` narrowed to the named flow cohort (distinct)."""
    table = {
        'all': qs,
        'open': qs.filter(status=S.OPEN),

        # ----- Screening -----
        # Unfit = never shortlisted (rejected at / before shortlisting)
        'unfit': qs.filter(status__in=TERMINAL).exclude(_reached(S.SHORTLISTED)),
        'ever_shortlisted': qs.filter(_reached(S.SHORTLISTED)),

        # ----- Call -----
        # Every call-stage bucket below is scoped to status=SHORTLISTED and keyed
        # off the LATEST logged call outcome only, so a candidate with several
        # calls over time (e.g. unable, then called back) lands in exactly one
        # bucket - the one matching their most recent attempt - not every bucket
        # any past call of theirs ever matched. Candidates who have moved on
        # (Round 1+), been rejected, or put on Hold are excluded here; they are
        # covered by 'shortlisted_after_call', 'rejected_after_call' and
        # 'hold_before_round1' respectively.
        # Yet to Call = no call logged yet, or last outcome was "Call back"
        # (still needs to be reached)
        'call_pending': _with_latest_call_outcome(qs.filter(status=S.SHORTLISTED)).filter(
            Q(latest_call_outcome__isnull=True) | Q(latest_call_outcome=OUT.CALLBACK)),
        # Cleared the call = moved on to Round 1 (signalled by the status action)
        'shortlisted_after_call': qs.filter(_reached(S.ROUND1)),
        # Attempted but not reached: last logged outcome was "Unable to connect"
        'unable_to_connect': _with_latest_call_outcome(qs.filter(status=S.SHORTLISTED)).filter(
            latest_call_outcome=OUT.UNABLE),
        # Reached on the call but no decision taken yet (never moved on to Round 1)
        'call_decision_pending': _with_latest_call_outcome(qs.filter(status=S.SHORTLISTED)).filter(
            latest_call_outcome=OUT.ATTENDED),
        # Rejected after call = terminal, reached shortlist, never reached Round 1
        'rejected_after_call': qs.filter(status__in=TERMINAL).filter(_reached(S.SHORTLISTED)).exclude(_reached(S.ROUND1)),
        # On Hold, taken from the call stage (shortlisted but not yet at Round 1) -
        # distinct from candidates held earlier (screening) or later (Round 1+),
        # who are counted in 'hold_before_shortlist' / the later-stage hold flows.
        'hold_before_round1': qs.filter(status=S.SCREENING_HOLD, hold_from_status=S.SHORTLISTED),

        # ----- Round 1 -----
        'r1_yet': qs.filter(status=S.ROUND1).exclude(interviews__round_type=R1),
        # Cleared R1 = a R1 interview passed OR ever reached the Interview (R2) stage
        'r1_cleared': qs.filter(Q(interviews__round_type=R1, interviews__result=PASS) | _reached(S.INTERVIEW)),
        'r1_scheduled': qs.filter(_upcoming([R1])),
        'r1_no_show': qs.filter(interviews__round_type=R1, interviews__status=CANC),
        'r1_decision_pending': qs.filter(status=S.ROUND1).filter(_decision_pending([R1])),
        'rejected_after_round1': qs.filter(status__in=TERMINAL).filter(_reached(S.ROUND1)).exclude(_reached(S.INTERVIEW)),
        # On Hold, taken after clearing Round 1 (before Round 2)
        'hold_before_round2': qs.filter(status=S.SCREENING_HOLD, hold_from_status=S.ROUND1),

        # ----- Round 2 (the final interview round) -----
        'r2_yet': qs.filter(status=S.INTERVIEW).exclude(interviews__round_type__in=NON_R1),
        'r2_cleared': qs.filter(Q(interviews__round_type__in=NON_R1, interviews__result=PASS)
                                | _reached(S.FINAL_SELECTION) | _reached(S.HIRED)),
        'r2_scheduled': qs.filter(_upcoming(NON_R1)),
        'r2_no_show': qs.filter(interviews__round_type__in=NON_R1, interviews__status=CANC),
        'r2_decision_pending': qs.filter(status=S.INTERVIEW).filter(_decision_pending(NON_R1)),
        'rejected_after_round2': qs.filter(status__in=TERMINAL).filter(_reached(S.INTERVIEW)).exclude(_reached(S.FINAL_SELECTION)),
        # On Hold, taken after clearing Round 2 (before the final decision)
        'hold_before_final': qs.filter(status=S.SCREENING_HOLD, hold_from_status=S.INTERVIEW),

        # ----- Final decision / Offer -----
        # On Hold = final decision pending
        'on_hold': qs.filter(Q(status=S.FINAL_SELECTION) | Q(is_on_hold=True)),
        'hired': qs.filter(Q(status=S.HIRED) | _reached(S.HIRED)),
        'rejected_after_final': qs.filter(status__in=TERMINAL).filter(_reached(S.FINAL_SELECTION)).exclude(_reached(S.HIRED)),
        # On Hold, taken after reaching the final decision stage (screening-hold
        # status, not the is_on_hold offer flag above)
        'hold_after_final': qs.filter(status=S.SCREENING_HOLD, hold_from_status=S.FINAL_SELECTION),

        # ----- terminal (used by the candidate list tabs) -----
        'rejected': qs.filter(status=S.REJECTED),
        'blacklisted': qs.filter(status=S.BLACKLISTED),
        'screening_hold': qs.filter(status=S.SCREENING_HOLD),
        # On hold and never reached Shortlisted (held straight out of screening) -
        # distinct from candidates held after clearing shortlist/round1/etc, who
        # are already counted in 'ever_shortlisted'.
        'hold_before_shortlist': qs.filter(status=S.SCREENING_HOLD, hold_from_status=S.OPEN),
    }
    result = table.get(flow)
    if result is None:
        return qs
    return result.distinct()


def flow_count(qs, flow):
    return flow_filter(qs, flow).count()
