"""Suggests a Reject Reason for an old rejection from its free-text remarks -
used once to sort rejections made before Reject Reasons existed (see the
suggest_reject_reasons management command). Plain keyword rules, checked in
priority order (the most specific first); no match -> Other. HR reviews the
suggestions before anything is saved.
"""
import re

from . import reject_reasons as R

# (code, [patterns]) - first matching rule wins. Patterns are regexes matched
# against the lower-cased remarks.
RULES = [
    (R.DUPLICATE, [r'auto-rejected: (duplicate|applied again)', r'\bduplicate\b']),
    ('POSITION_CLOSED', [r'position (is )?(closed|filled|on hold)', r'requirement (is )?closed', r'\bfilled\b',
                         r'no (opening|vacancy|vacancies)', r'role (is )?closed', r'hired (someone|another)',
                         r'vacancy closed']),
    ('WITHDREW', [r'not interested', r'withdr[ae]w', r'backed out', r'\bdeclined?\b', r'no response',
                  r'not respond', r"didn'?t respond", r'not reachable', r'unreachable', r'not turned up',
                  r'did not turn up', r"didn'?t turn up", r'no[- ]show', r'\brnr\b', r'not picking',
                  r'not answering', r'switched off', r'not attend', r'did not attend', r'not joining']),
    ('NOTICE_PERIOD', [r'notice period', r'\bnotice\b', r'availability', r'immediate joiner', r'joining date',
                       r'serving notice', r'\bnp\b']),
    ('CTC_MISMATCH', [r'\bctc\b', r'\bectc\b', r'salary', r'compensation', r'\bpackage\b', r'budget',
                      r'expectation', r'\bhike\b', r'\bpay\b']),
    ('RELOCATION', [r'relocat', r'location', r'distance', r'commut', r'not willing to (move|shift)',
                    r'far from', r'\bnative\b', r'travel']),
    ('OVERQUALIFIED', [r'over ?qualified', r'too senior', r'over ?experienced', r'more experienced than']),
    ('UNDERQUALIFIED', [r'under ?qualified', r'less experience', r'lack of experience', r'not enough experience',
                        r'insufficient experience', r'experience is less', r'low experience', r'\bfresher\b',
                        r'too junior', r'no experience', r'limited experience', r'less exp\b']),
    ('COMMUNICATION', [r'communication', r'\benglish\b', r'fluen', r'articulat', r'\bspeak', r'confidence']),
    ('ATTITUDE', [r'attitude', r'behaviou?r', r'\brude\b', r'arrogan', r'unprofessional', r'not serious']),
    ('EDUCATION', [r'qualification', r'\bdegree\b', r'education', r'graduat', r'\bmba\b', r'\bb\.?tech\b']),
    ('TECHNICAL_GAP', [r'technical', r'\bskills?\b', r'\bsql\b', r'\bexcel\b', r'python', r'power ?bi',
                       r'knowledge', r'coding', r'\btools?\b', r'aptitude', r'assessment', r'\btest\b',
                       r'fundamental', r'concepts?']),
    (R.INTERVIEW_PERFORMANCE, [r'interview (failed|not cleared)', r'failed', r'not cleared', r'performance',
                               r'did not perform', r'poor interview', r'not up to the mark', r'below expectation']),
    ('ROLE_FIT', [r'not (a )?(good )?fit', r'not suitable', r'unsuitable', r'mismatch', r'not relevant',
                  r'irrelevant', r'not matching', r'does not match', r"doesn'?t match", r'\bdomain\b',
                  r'profile (not|is not)', r'not aligned']),
]
_COMPILED = [(code, [re.compile(p) for p in patterns]) for code, patterns in RULES]


def suggest(remarks):
    """Reject Reason code for these remarks (OTHER when nothing matches)."""
    text = (remarks or '').lower()
    if not text.strip():
        return R.OTHER
    for code, patterns in _COMPILED:
        if any(p.search(text) for p in patterns):
            return code
    return R.OTHER
