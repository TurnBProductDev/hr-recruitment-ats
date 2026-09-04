from django import template
from django.urls import NoReverseMatch, reverse

register = template.Library()

# Static breadcrumb hierarchy for HR-facing pages: url_name -> (label, parent_url_name).
# parent_url_name must always be reversible with no arguments (a list/dashboard
# page) - a page whose real parent needs an object (a specific candidate, job,
# interview...) instead sets back_url/back_label in its own context (the
# app's existing "where did I actually come from" mechanism), which this tag
# prefers over the static parent when present. A url_name with no entry here
# falls back to the old plain Back control - see _breadcrumbs.html.
BREADCRUMB_REGISTRY = {
    'hr_reports': ('Reports', None),
    'candidate_repository': ('Candidates', None),
    'candidate_all': ('All Candidates', 'candidate_repository'),
    'candidate_general_applications': ('General Applications', 'candidate_repository'),
    'candidate_future_prospects': ('Future Prospects', 'candidate_repository'),
    'candidate_timeline': ('Candidate Profile', None),
    'candidate_edit': ('Edit Candidate', None),
    'candidate_bulk_upload': ('Bulk Upload', None),
    'candidate_bulk_progress': ('Progress', 'candidate_bulk_upload'),
    'candidate_score': ('Score Candidates', None),
    'candidate_score_progress': ('Scoring Progress', 'candidate_score'),
    'job_manage_list': ('Vacancies', None),
    'job_add': ('Add Vacancy', 'job_manage_list'),
    'job_edit': ('Edit Vacancy', 'job_manage_list'),
    'interview_scheduler': ('Interviews', None),
    'interview_schedule': ('Schedule Interview', None),
    'interview_reschedule': ('Reschedule Interview', None),
    'interview_result': ('Interview Result', 'interview_scheduler'),
    'daily_action_drilldown': ('Action Detail', None),
}


def _strip_back_prefix(label):
    """'Back to Bulk Upload' -> 'Bulk Upload' - back_label was written for a
    button, this reuses it as a breadcrumb crumb instead."""
    if label and label.startswith('Back to '):
        return label[len('Back to '):]
    return label


@register.inclusion_tag('dashboard/_breadcrumbs.html', takes_context=True)
def breadcrumbs(context):
    request = context.get('request')
    match = getattr(request, 'resolver_match', None) if request else None
    url_name = match.url_name if match else None
    back_url = context.get('back_url')
    back_label = context.get('back_label')

    if not url_name or url_name == 'hr_dashboard' or url_name not in BREADCRUMB_REGISTRY:
        return {'crumbs': None, 'back_url': back_url, 'back_label': back_label}

    trail = [{'label': 'Home', 'url': reverse('hr_dashboard')}]
    label, parent_name = BREADCRUMB_REGISTRY[url_name]

    if back_url:
        trail.append({'label': _strip_back_prefix(back_label) or 'Back', 'url': back_url})
    elif parent_name:
        parent_label, _ = BREADCRUMB_REGISTRY.get(parent_name, (parent_name, None))
        try:
            trail.append({'label': parent_label, 'url': reverse(parent_name)})
        except NoReverseMatch:
            pass

    trail.append({'label': context.get('breadcrumb_current') or label, 'url': None})
    return {'crumbs': trail, 'back_url': back_url, 'back_label': back_label}
