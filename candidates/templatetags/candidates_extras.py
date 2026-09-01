from django import template

register = template.Library()


@register.filter
def badge_class(status):
    return f"badge-status badge-{str(status).lower()}"


@register.filter
def dict_get(mapping, key):
    return mapping.get(key)
