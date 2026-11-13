from django import template

register = template.Library()


@register.filter
def badge_class(status):
    return f"badge-status badge-{str(status).lower()}"


@register.filter
def dict_get(mapping, key):
    return mapping.get(key)


@register.filter
def score_badge_class(score):
    """Colour a match score badge: green (strong fit), amber (borderline), red (weak)."""
    if score is None:
        return 'bg-secondary'
    if score >= 70:
        return 'bg-success'
    if score >= 40:
        return 'bg-warning text-dark'
    return 'bg-danger'


@register.filter
def score_band(score):
    """HR's band name for a match score (prompts.match_scoring.SCORE_BANDS):
    85+ Strong Fit, 70+ Strong Potential - Minor Gaps, 55+ Potential -
    Significant Gaps, 40+ Consider, below 40 Unfit."""
    from prompts.match_scoring import SCORE_BANDS
    if score is None:
        return ''
    return next(label for floor, label in SCORE_BANDS if score >= floor)


FACTOR_LABELS = {
    'core_function_experience': 'Core experience', 'domain_relevance': 'Domain',
    'seniority_fit': 'Seniority', 'evidence_of_results': 'Results',
    'education_and_skills': 'Education & skills',
}


@register.filter
def score_factors(breakdown):
    """[(label, 0-10 rating)] from a match breakdown's `factors`, in display
    order - empty for scores made before factor ratings existed."""
    factors = (breakdown or {}).get('factors') or {}
    return [(label, factors[key]) for key, label in FACTOR_LABELS.items() if key in factors]


@register.filter
def score_tier(score):
    """Same strong/borderline/weak cutoffs as score_badge_class, named for the
    score-meter bar's colour classes (.score-fill-high/-mid/-low)."""
    if score is None:
        return ''
    if score >= 70:
        return 'high'
    if score >= 40:
        return 'mid'
    return 'low'
