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


def _normalise_matched_skills(matched_skills):
    pairs = []
    for item in matched_skills or []:
        if isinstance(item, dict):
            pairs.append((item.get('skill', ''), item.get('evidence', '')))
        elif item:
            pairs.append((str(item), ''))
    return pairs


@register.filter
def matched_skill_pairs(matched_skills):
    """(skill, evidence) tuples from a match_breakdown matched_skills list.
    Handles both the current {skill, evidence} object format and the plain
    skill-name strings stored before evidence-backed matching was added, so
    already-scored candidates still render correctly."""
    return _normalise_matched_skills(matched_skills)


@register.filter
def skill_names(matched_skills):
    """Comma-separated skill names, from either matched_skills format."""
    return ', '.join(skill for skill, _ in _normalise_matched_skills(matched_skills) if skill)


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
