"""Suggests a Reject Reason for an old rejection from its free-text remarks -
used once to sort rejections made before Reject Reasons existed (see the
suggest_reject_reasons management command). Plain keyword rules, checked in
priority order (the most specific first); no match -> Other. HR reviews the
suggestions before anything is saved.
"""
import re

from . import reject_reasons as R

CITIES = r'chennai|mumbai|bangalore|bengaluru|hyderabad|pune|delhi|bhubaneshwar|kolkata|noida|gurgaon'
PERIOD = r'\d+(\.\d+)? ?(yrs?|years?|months?)'

# (code, [patterns]) - first matching rule wins. Patterns are regexes matched
# against the lower-cased remarks.
RULES = [
    (R.DUPLICATE, [r'auto-rejected: (duplicate|applied again)', r'\bduplicate\b', r'already attended']),
    ('POSITION_CLOSED', [r'position (is )?(closed|filled|on hold)', r'requirement (is )?closed', r'\bfilled\b',
                         r'no (opening|vacancy|vacancies)', r'role (is )?closed', r'hired (someone|another)',
                         r'vacancy closed']),
    ('WITHDREW', [r'not interested', r'withdr[ae]w', r'backed out', r'\bdeclined?\b', r'no response',
                  r'not respond', r"didn'?t respond", r'not reachable', r'unreachable', r'not turned up',
                  r'did not turn up', r"didn'?t turn up", r'no[- ]show', r'\brnr\b', r'not picking',
                  r'not answering', r'switched off', r'not attend', r'did not attend', r'not joining',
                  r'not looking', r'got (a|another) job', r'accepted (an)?other offer', r'not connecting',
                  r'not (willing|available) to attend']),
    ('NOTICE_PERIOD', [r'notice period', r'\bnotice\b', r'availability', r'immediate joiner', r'joining date',
                       r'serving notice', r'\bnp\b']),
    ('CTC_MISMATCH', [r'\bctc\b', r'\bectc\b', r'salary', r'compensation', r'\bpackage\b', r'budget',
                      r'expectation', r'\bhike\b', r'\bpay\b']),
    ('RELOCATION', [r'relocat', r'location', r'distance', r'commut', r'not willing to (move|shift)',
                    r'far from', r'\bnative\b', r'travel', r'\bbased\b', rf'\b({CITIES})\b']),
    ('OVERQUALIFIED', [r'over ?qualified', r'too senior', r'over ?experienced', r'more experienced than',
                       r'high experience', r'too much experience']),
    ('UNDERQUALIFIED', [r'under ?qualified', r'less experience', r'lack of experience', r'not enough experience',
                        r'insufficient experience', r'experience is less', r'low experience', r'\bfresher\b',
                        r'too junior', r'no experience', r'limited experience', r'less exp\b', r'^\s*no exp\b',
                        rf'^\s*{PERIOD} (exp|experience)\b', rf'\bonly {PERIOD}', rf'{PERIOD} (exp|experience) only',
                        r'only internship']),
    ('COMMUNICATION', [r'communication', r'\benglish\b', r'fluen', r'articulat', r'\bspeak', r'confidence',
                       r'\blanguage\b', r'grammat', r'\bslang\b', r'filler']),
    ('ATTITUDE', [r'attitude', r'behaviou?r', r'\brude\b', r'arrogan', r'unprofessional', r'not serious',
                  r'no drive', r'drive (was )?not there', r'low energy', r'not energetic', r'beating around']),
    ('EDUCATION', [r'qualification', r'\bdegree\b', r'education', r'graduat', r'\bmba\b', r'\bb\.?tech\b',
                   r'backlog', r'\bmarks?\b', r'percent', r'\bno ug\b', r'pursuing', r'pass ?out', r'bachelor of']),
    ('TECHNICAL_GAP', [r'technical', r'\bskills?\b', r'\bsql\b', r'\bexcel\b', r'python', r'power ?bi',
                       r'knowledge', r'coding', r'\btools?\b', r'aptitude', r'assessment', r'\btest\b',
                       r'fundamental', r'concepts?']),
    (R.INTERVIEW_PERFORMANCE, [r'interview (failed|not cleared)', r'failed', r'not cleared', r'performance',
                               r'did not perform', r'poor interview', r'not up to the mark', r'below expectation']),
    ('ROLE_FIT', [r'not (a )?(good )?fit', r'not suitable', r'unsuitable', r'mismatch', r'not relevant',
                  r'irrelevant', r'not matching', r'does not match', r"doesn'?t match", r'\bdomain\b',
                  r'not aligned', r'non[- ]?(relevant|related)', r'no (relevant|related)', r'not apt',
                  r'not good for', r'\bno (ai|it|sales|pm|tech)\b', r'(exp|experience) only',
                  r'^\s*only .*(exp|experience)', r'\bonky\b', r'experience (into|in) ', r'can consider for',
                  r'applied by mistake', r'no context', r'profile', r'\bretail exp', r'data engineer']),
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
