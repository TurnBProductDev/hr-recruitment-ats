from django import forms

from candidates.models import ScoringCriteria
from HR_management.widgets import BareClearableFileInput

from .models import Job


class BootstrapFormMixin:
    def _add_bootstrap_classes(self):
        for field in self.fields.values():
            widget = field.widget
            if isinstance(widget, forms.CheckboxInput):
                widget.attrs.setdefault('class', 'form-check-input')
            elif isinstance(widget, (forms.Select, forms.SelectMultiple)):
                widget.attrs.setdefault('class', 'form-select')
            else:
                widget.attrs.setdefault('class', 'form-control')


class JobForm(BootstrapFormMixin, forms.ModelForm):
    # Not a Job field - lives on candidates.models.ScoringCriteria (one row
    # per Job, same place the dedicated Scoring Criteria page reads/writes -
    # see that page's own docstring for how it reaches the scoring prompt).
    # Offered here too as a convenience so it can be set right when the
    # vacancy is created, not only after the fact on a separate page.
    # HR_ADMIN and Recruiter only (same two roles who can reach this form at
    # all - see jobs/views.py's _can_set_scoring_criteria, which is what
    # actually persists it and is the server-side half of job_form.html only
    # rendering this field for them). The dedicated Scoring Criteria page
    # stays HR_ADMIN-only regardless.
    extra_scoring_criteria = forms.CharField(
        label='Extra Requirements', required=False,
        widget=forms.Textarea(attrs={
            'rows': 4,
            'placeholder': 'e.g. Give extra weight to candidates with hands-on AI/ML project '
                          'experience. Treat prior IT industry experience as a strong positive.',
        }),
        help_text='Layered on top of the base scoring rubric whenever Score Candidates runs for '
                  'this role - same as the dedicated Scoring Criteria page.')

    class Meta:
        model = Job
        # job_type and must_have_requirements are deliberately excluded -
        # Score Candidates judges fit holistically now (see
        # candidates/match_scoring.py) rather than weighing skills/experience/
        # education by a per-job-type rubric or gating on a rigid must-have
        # checklist; the model already treats a stated must-have as a serious
        # mark against a candidate when it isn't evidenced. Both fields/
        # columns stay on the model for old rows; nothing reads job_type
        # anymore, and must_have_requirements is only ever read from existing
        # data now (nothing new can be entered here).
        fields = ['job_code', 'title', 'location', 'openings', 'description', 'requirements',
                  'status', 'opening_date', 'closing_date', 'jd_file']
        labels = {'job_code': 'Job Code'}
        widgets = {
            'job_code': forms.TextInput(attrs={'placeholder': 'e.g. HRBP-2026 (auto if blank)'}),
            'description': forms.Textarea(attrs={'rows': 4}),
            'requirements': forms.Textarea(attrs={'rows': 4}),
            'opening_date': forms.DateInput(attrs={'type': 'date'}),
            'closing_date': forms.DateInput(attrs={'type': 'date'}),
            # Plain <input type=file> - job_form.html renders its own
            # filename/View/Clear row above this, so the default widget's
            # "Currently: ... / Clear / Change:" markup would just duplicate it.
            'jd_file': BareClearableFileInput,
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields['extra_scoring_criteria'].initial = ScoringCriteria.load_for(self.instance).extra_instructions
        self._add_bootstrap_classes()

    def clean_job_code(self):
        # Normalise whitespace; blank is allowed and triggers auto-generation on save.
        code = (self.cleaned_data.get('job_code') or '').strip()
        if not code:
            return code
        # Case-insensitive uniqueness so 'HRBP' and 'hrbp' can't both exist.
        clash = Job.objects.filter(job_code__iexact=code)
        if self.instance.pk:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise forms.ValidationError('This job code is already in use. Choose a different one.')
        return code
