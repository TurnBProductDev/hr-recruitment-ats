from django import forms
from django.forms import inlineformset_factory

from jobs.models import Job

from .models import (
    CANONICAL_SOURCES,
    Candidate,
    CandidateEducation,
    CandidateExperience,
    CommunicationLog,
    Note,
)


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


class CandidateApplicationForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Candidate
        fields = [
            'full_name', 'email', 'phone', 'dob', 'current_location',
            'qualification', 'institution', 'last_role', 'last_company',
            'total_experience_years', 'skills',
            'linkedin', 'portfolio_url', 'notice_period', 'expected_salary',
            'current_salary', 'resume_blob_url',
        ]
        labels = {
            'qualification': 'Highest Qualification',
            'institution': 'Institution',
            'resume_blob_url': 'Resume (PDF/DOC)',
            'skills': 'Key Skills',
        }
        widgets = {
            'dob': forms.DateInput(attrs={'type': 'date'}),
            'skills': forms.Textarea(attrs={'rows': 2, 'placeholder': 'e.g. Python, SQL, Excel'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Resume upload is mandatory only when applying (a new candidate),
        # not when HR edits an existing one (who may only have a CV link).
        self.fields['resume_blob_url'].required = not (self.instance and self.instance.pk)
        self._add_bootstrap_classes()


class CandidateEducationForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = CandidateEducation
        fields = ['qualification', 'institution', 'year_completed', 'percentage', 'specialization']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._add_bootstrap_classes()


class CandidateExperienceForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = CandidateExperience
        fields = ['company_name', 'designation', 'start_date', 'end_date', 'total_experience', 'skills']
        widgets = {
            'start_date': forms.DateInput(attrs={'type': 'date'}),
            'end_date': forms.DateInput(attrs={'type': 'date'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._add_bootstrap_classes()


class CandidateNoteForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Note
        fields = ['text']
        widgets = {'text': forms.Textarea(attrs={'rows': 2, 'placeholder': 'Add a note about this candidate...'})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._add_bootstrap_classes()


class CommunicationLogForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = CommunicationLog
        fields = ['channel', 'outcome', 'message']
        labels = {'message': 'Remarks'}
        widgets = {'message': forms.Textarea(attrs={'rows': 2, 'placeholder': 'Enter remarks...'})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['channel'].choices = [
            (CommunicationLog.Channel.PHONE, 'Phone'),
            (CommunicationLog.Channel.INTERVIEW, 'Interview'),
        ]
        self.fields['outcome'].required = False
        self.fields['outcome'].choices = [
            ('', '— Outcome —'),
            (CommunicationLog.Outcome.UNABLE, 'Unable to connect'),
            (CommunicationLog.Outcome.CALLBACK, 'Call back'),
            (CommunicationLog.Outcome.NOT_TURNED_UP, 'Not turned up'),
            (CommunicationLog.Outcome.ATTENDED, 'Attended'),
        ]
        self._add_bootstrap_classes()


class BulkUploadForm(BootstrapFormMixin, forms.Form):
    job = forms.ModelChoiceField(queryset=Job.objects.filter(status=Job.Status.OPEN), label='Vacancy')
    # Canonical source names — must match the values already in the database and
    # the Logic App intake proc, otherwise the dashboard splits one source in two
    # (e.g. 'LinkedIn' vs 'Linked In'). See CANONICAL_SOURCES below.
    source = forms.ChoiceField(choices=[(s, s) for s in CANONICAL_SOURCES])

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._add_bootstrap_classes()


EducationFormSet = inlineformset_factory(
    Candidate, CandidateEducation, form=CandidateEducationForm,
    extra=1, can_delete=True,
)

ExperienceFormSet = inlineformset_factory(
    Candidate, CandidateExperience, form=CandidateExperienceForm,
    extra=1, can_delete=True,
)
