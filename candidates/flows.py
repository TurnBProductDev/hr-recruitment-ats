"""Shared stage-flow filters for the funnel dashboard and the candidate list.

A 'flow' names a derived cohort (e.g. 'r1_cleared'). Using one definition in
both places guarantees the dashboard count and the drill-down list always match.

"Reached a stage" is read from the status history, so a candidate counts as
having cleared a stage even if they were later rejected/hired (funnel view).
"""
from django.db.models import Q

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


def _reached(stage):
    return Q(history__new_status=stage)


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
        # Yet to Call = shortlisted and either no call logged yet, or last outcome
        # was "Call back" (still needs to be reached)
        'call_pending': qs.filter(status=S.SHORTLISTED).filter(
            Q(communication_logs__isnull=True) | Q(communication_logs__outcome=OUT.CALLBACK)),
        # Cleared the call = moved on to Round 1 (signalled by the status action)
        'shortlisted_after_call': qs.filter(_reached(S.ROUND1)),
        'unable_to_connect': qs.filter(communication_logs__outcome=OUT.UNABLE),
        # Attempted but not reached: still shortlisted with an "Unable to connect"
        # logged and no callback promised or successful call. Without this they fall
        # out of 'call_pending' (which only counts un-attempted / callback) and
        # disappear from the funnel altogether.
        'to_recall': (qs.filter(status=S.SHORTLISTED, communication_logs__outcome=OUT.UNABLE)
                      .exclude(communication_logs__outcome__in=[OUT.CALLBACK, OUT.ATTENDED])),
        # Reached on the call but no decision taken yet (never moved on to Round 1)
        'called_pending': (qs.filter(status=S.SHORTLISTED, communication_logs__outcome=OUT.ATTENDED)
                           .exclude(communication_logs__outcome=OUT.CALLBACK)),
        # Rejected after call = terminal, reached shortlist, never reached Round 1
        'rejected_after_call': qs.filter(status__in=TERMINAL).filter(_reached(S.SHORTLISTED)).exclude(_reached(S.ROUND1)),

        # ----- Round 1 -----
        'r1_yet': qs.filter(status=S.ROUND1).exclude(interviews__round_type=R1),
        # Cleared R1 = a R1 interview passed OR ever reached the Interview (R2) stage
        'r1_cleared': qs.filter(Q(interviews__round_type=R1, interviews__result=PASS) | _reached(S.INTERVIEW)),
        'r1_scheduled': qs.filter(interviews__round_type=R1, interviews__status__in=[SCHED, RESCHED]),
        'r1_no_show': qs.filter(interviews__round_type=R1, interviews__status=CANC),
        'rejected_after_round1': qs.filter(status__in=TERMINAL).filter(_reached(S.ROUND1)).exclude(_reached(S.INTERVIEW)),

        # ----- Round 2 (the final interview round) -----
        'r2_yet': qs.filter(status=S.INTERVIEW).exclude(interviews__round_type__in=NON_R1),
        'r2_cleared': qs.filter(Q(interviews__round_type__in=NON_R1, interviews__result=PASS)
                                | _reached(S.FINAL_SELECTION) | _reached(S.HIRED)),
        'r2_scheduled': qs.filter(interviews__round_type__in=NON_R1, interviews__status__in=[SCHED, RESCHED]),
        'r2_no_show': qs.filter(interviews__round_type__in=NON_R1, interviews__status=CANC),
        'rejected_after_round2': qs.filter(status__in=TERMINAL).filter(_reached(S.INTERVIEW)).exclude(_reached(S.FINAL_SELECTION)),

        # ----- Final decision / Offer -----
        # On Hold = final decision pending
        'on_hold': qs.filter(Q(status=S.FINAL_SELECTION) | Q(is_on_hold=True)),
        'hired': qs.filter(Q(status=S.HIRED) | _reached(S.HIRED)),
        'rejected_after_final': qs.filter(status__in=TERMINAL).filter(_reached(S.FINAL_SELECTION)).exclude(_reached(S.HIRED)),

        # ----- terminal (used by the candidate list tabs) -----
        'rejected': qs.filter(status=S.REJECTED),
        'blacklisted': qs.filter(status=S.BLACKLISTED),
        'screening_hold': qs.filter(status=S.SCREENING_HOLD),
    }
    result = table.get(flow)
    if result is None:
        return qs
    return result.distinct()


def flow_count(qs, flow):
    return flow_filter(qs, flow).count()
