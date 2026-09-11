from django import forms


class BareClearableFileInput(forms.ClearableFileInput):
    """A ClearableFileInput that renders as a plain <input type=file> - none
    of the default widget's own "Currently: <link> / Clear / Change:" markup.
    Clearing still works: the widget's value_from_datadict looks for a
    checkbox named "<field>-clear" in POST data regardless of where in the
    page that checkbox lives, so the template renders its own file-details
    row (name/preview link/Clear checkbox) instead of this default one -
    see candidate_form.html's "View attached CV" row and job_form.html's
    "Job Description File" row."""
    template_name = 'django/forms/widgets/file.html'
