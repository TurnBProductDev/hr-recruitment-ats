"""Reject Reasons - the fixed list HR picks from whenever a candidate is
rejected (every stage, single or bulk). Stored as the short code on the
Rejected status-history row (CandidateStatusHistory.reject_reason); "Other"
also needs the typed reason (reject_reason_other). Any extra remarks stay
in the row's normal `remarks`.

DUPLICATE and BLACKLISTED_EARLIER are system-only: the 6-month same-vacancy
rule and the blacklist rule (candidates/duplicate_rule.py) use them; they're
never offered to HR.
"""
OTHER = 'OTHER'
DUPLICATE = 'DUPLICATE'
BLACKLISTED_EARLIER = 'BLACKLISTED_EARLIER'
INTERVIEW_PERFORMANCE = 'INTERVIEW_PERFORMANCE'

# (code, label) in the order HR sees them.
CHOICES = [
    ('COMMUNICATION', 'Communication Skills - Insufficient'),
    ('OVERQUALIFIED', 'Experience Level - Overqualified'),
    ('UNDERQUALIFIED', 'Experience Level - Underqualified'),
    ('CTC_MISMATCH', 'Compensation Expectations (CTC) Mismatch'),
    ('ATTITUDE', 'Attitude / Behavioral Concerns'),
    ('RELOCATION', 'Relocation Constraints'),
    ('TECHNICAL_GAP', 'Technical Skills Gap'),
    ('ROLE_FIT', 'Role Fit Mismatch'),
    ('NOTICE_PERIOD', 'Availability / Notice Period Mismatch'),
    ('EDUCATION', 'Educational Qualification Mismatch'),
    (INTERVIEW_PERFORMANCE, 'Interview Performance - Unsatisfactory'),
    ('WITHDREW', 'Candidate Withdrew'),
    ('POSITION_CLOSED', 'Position Closed / Filled'),
    (OTHER, 'Other'),
]
SYSTEM_CHOICES = [(DUPLICATE, 'Duplicate Application'),
                  (BLACKLISTED_EARLIER, 'Blacklisted Earlier')]
ALL_CHOICES = CHOICES + SYSTEM_CHOICES
LABELS = dict(ALL_CHOICES)
SELECTABLE = {code for code, _ in CHOICES}

REASON_REQUIRED = 'Pick a Reject Reason.'
OTHER_REQUIRED = 'Type the reason when choosing "Other".'


def label(code, other=''):
    """'Technical Skills Gap', 'Other: Salary freeze', or '' if no code."""
    if not code:
        return ''
    text = LABELS.get(code, code)
    if code == OTHER and other:
        return f'{text}: {other}'
    return text


def from_post(data):
    """(code, other_text, error) from a submitted reject form."""
    code = (data.get('reject_reason') or '').strip()
    other = (data.get('reject_reason_other') or '').strip()[:255]
    if code not in SELECTABLE:
        return None, '', REASON_REQUIRED
    if code == OTHER and not other:
        return None, '', OTHER_REQUIRED
    return code, (other if code == OTHER else ''), None
