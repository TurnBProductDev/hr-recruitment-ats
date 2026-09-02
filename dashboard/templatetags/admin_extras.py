from django import template

register = template.Library()


@register.filter
def total_models(app_list):
    return sum(len(app["models"]) for app in app_list)


@register.filter
def model_count(model_dict):
    try:
        return model_dict["model"]._default_manager.count()
    except Exception:
        return ""
