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
