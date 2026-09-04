from django import forms

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
    class Meta:
        model = Job
        fields = ['job_code', 'title', 'location', 'openings', 'description', 'requirements',
                  'job_type', 'must_have_requirements', 'status', 'opening_date', 'closing_date', 'jd_file']
        labels = {'job_code': 'Job Code', 'job_type': 'Scoring Profile'}
        widgets = {
            'job_code': forms.TextInput(attrs={'placeholder': 'e.g. HRBP-2026 (auto if blank)'}),
            'description': forms.Textarea(attrs={'rows': 4}),
            'requirements': forms.Textarea(attrs={'rows': 4}),
            'must_have_requirements': forms.Textarea(
                attrs={'rows': 3, 'placeholder': 'One requirement per line, e.g.\nMust have a valid driving licence\n5+ years in a regulated industry'}),
            'opening_date': forms.DateInput(attrs={'type': 'date'}),
            'closing_date': forms.DateInput(attrs={'type': 'date'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
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
