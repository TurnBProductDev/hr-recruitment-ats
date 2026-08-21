from django import forms
from django.contrib.auth import get_user_model

from candidates.permissions import INTERVIEWER

from .models import Interview, open_interview_message


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


class InterviewForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Interview
        fields = ['round_type', 'interviewer', 'scheduled_date', 'mode', 'meeting_link']
        widgets = {
            'scheduled_date': forms.DateTimeInput(attrs={'type': 'datetime-local'}),
        }

    def __init__(self, *args, candidate=None, **kwargs):
        super().__init__(*args, **kwargs)
        # On a reschedule the candidate comes from the interview being edited.
        self.candidate = candidate or (self.instance.candidate if self.instance.candidate_id else None)
        # Only people in the Interviewer group are assignable, ordered by name.
        User = get_user_model()
        self.fields['interviewer'].queryset = (
            User.objects.filter(groups__name=INTERVIEWER).order_by('first_name', 'last_name'))
        self.fields['interviewer'].label_from_instance = (
            lambda u: u.get_full_name() or u.username)
        self.fields['interviewer'].empty_label = 'Unassigned'
        self._add_bootstrap_classes()

    def clean(self):
        # One open interview per candidate. Rescheduling the open interview is
        # fine (it is excluded), scheduling a second one alongside it is not.
        cleaned = super().clean()
        if self.candidate:
            clash = Interview.open_for(self.candidate)
            if self.instance.pk:
                clash = clash.exclude(pk=self.instance.pk)
            clash = clash.first()
            if clash:
                raise forms.ValidationError(open_interview_message(clash))
        return cleaned


class InterviewResultForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Interview
        fields = ['status', 'result', 'score', 'feedback']
        widgets = {'feedback': forms.Textarea(attrs={'rows': 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._add_bootstrap_classes()
