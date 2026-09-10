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
