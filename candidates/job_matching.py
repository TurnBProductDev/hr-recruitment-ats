"""Match an applicant's free-text role wording to one of our real open
vacancies, for the careers-mailbox intake (see candidates.views.CVExtractAPIView
and sql/sp_intake_add_candidate.sql). Exact title matching alone was missing
most real applications - see the cases this was built from:

    "Sales and Marketing Manager"             -> "Sales and Marketing Manager - UAE"
    "HRBP Role" / "HR BP ROLE"                -> "HRBP"
    "Sales Associate - Analytics & AI"        -> "Sales Associate"
    "Marketing Associate and Sales Associate" -> "Marketing Associate"

Deliberately NOT a character-similarity score (e.g. difflib ratio on the raw
strings) - that was tried first and scored "Accountant" at 0.58 similarity to
"Analytics Consultant AI" (coincidental letter overlap), well above the score
needed to catch the real cases above. A wrong silent auto-map is worse than
leaving a candidate in General Application for HR to map by hand, so this
uses substring containment instead: does one string's letters-only form
appear literally inside the other's? That still catches every case above
(collapsing "HR BP ROLE" and "HRBP Role" to the same "hrbprole"/"hrbp"
comparison), but requires an exact contiguous match rather than a fuzzy
score, which real unrelated role names essentially never produce by accident.

Scoped to currently OPEN, non-archived jobs only (unlike the SQL exact-match
fallback, which matches a job in any status) - a fuzzy match is inherently
less certain than an exact one, so it shouldn't be the thing that lands a new
applicant on a vacancy that isn't even hiring any more.
"""
import re

from jobs.models import Job

# Below this many letters/digits, a string is too generic to safely match on
# (e.g. "HR", "AI") - "HRBP" (4 chars) is the shortest real title this needs
# to catch, so this can't be set any higher without breaking that case.
MIN_LEN = 4


def _normalize(text):
    return re.sub(r'[^a-z0-9]', '', (text or '').lower())


def match_job_by_title(role_applied):
    """Best-matching open Job for this free-text role, or None. Never raises."""
    applied_norm = _normalize(role_applied)
    if len(applied_norm) < MIN_LEN:
        return None

    best = None
    best_len = 0
    for job in Job.objects.filter(status=Job.Status.OPEN, is_archived=False):
        title_norm = _normalize(job.title)
        if len(title_norm) < MIN_LEN:
            continue
        if title_norm in applied_norm or applied_norm in title_norm:
            # Several open titles can each be a substring of a compound
            # applied string (e.g. "Marketing Associate and Sales
            # Associate" contains both) - the longer/more specific title
            # is the more likely intended one.
            if len(title_norm) > best_len:
                best, best_len = job, len(title_norm)
    return best
