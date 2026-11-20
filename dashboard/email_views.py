"""Emails: an in-app page (next to Users) where HR can edit the wording of
every outbound email template (email_templates.models.EmailTemplate) without
going into Django admin. Rows are fixed - each is tied to one place in the
code - so there's no Add/Delete, only Edit."""
import re

from django import forms
from django.contrib import messages
from django.urls import reverse
from django.views.generic import ListView, UpdateView

from candidates.permissions import HIRING_MANAGER, HR_ADMIN, RECRUITER, GroupRequiredMixin
from email_templates.models import EmailTemplate

_TOKEN = re.compile(r'\{(\w+)\}')


def allowed_placeholders(template):
    """The {placeholders} this template supports, read from its description."""
    return sorted(set(_TOKEN.findall(template.description or '')))


class EmailTemplateForm(forms.ModelForm):
    class Meta:
        model = EmailTemplate
        fields = ['subject', 'body']
        widgets = {
            'subject': forms.TextInput(attrs={'class': 'form-control'}),
            'body': forms.Textarea(attrs={'class': 'form-control font-monospace', 'rows': 20}),
        }

    def clean(self):
        cleaned = super().clean()
        allowed = set(allowed_placeholders(self.instance))
        if not allowed:
            return cleaned
        for field in ('subject', 'body'):
            unknown = sorted(set(_TOKEN.findall(cleaned.get(field) or '')) - allowed)
            if unknown:
                self.add_error(field, (
                    'Not a placeholder this email supports: ' + ', '.join('{' + u + '}' for u in unknown)
                    + '. It would be sent as-is. Use one of: ' + ', '.join('{' + a + '}' for a in sorted(allowed))
                    + '.'))
        return cleaned


class EmailTemplateListView(GroupRequiredMixin, ListView):
    model = EmailTemplate
    template_name = 'dashboard/email_list.html'
    context_object_name = 'templates'
    allowed_groups = (HR_ADMIN, RECRUITER, HIRING_MANAGER)

    def get_queryset(self):
        return EmailTemplate.objects.select_related('updated_by').order_by('label')


class EmailTemplateUpdateView(GroupRequiredMixin, UpdateView):
    model = EmailTemplate
    form_class = EmailTemplateForm
    template_name = 'dashboard/email_form.html'
    allowed_groups = (HR_ADMIN, RECRUITER, HIRING_MANAGER)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['placeholders'] = allowed_placeholders(self.object)
        ctx['back_url'] = reverse('email_template_list')
        ctx['back_label'] = 'Emails'
        return ctx

    def form_valid(self, form):
        form.instance.updated_by = self.request.user
        response = super().form_valid(form)
        messages.success(self.request, f'"{self.object.label}" saved - it applies to the next email sent.')
        return response

    def get_success_url(self):
        return reverse('email_template_list')
