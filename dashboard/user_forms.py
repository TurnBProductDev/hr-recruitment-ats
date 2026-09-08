from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError

from candidates.permissions import ALL_GROUPS, HIRING_MANAGER, HR_ADMIN, INTERVIEWER, RECRUITER

User = get_user_model()

# Friendly labels for the 4 groups candidates/permissions.py defines. The
# underlying Group name stays exactly what GroupRequiredMixin checks against
# everywhere else in the app ("HR Admin", not "Admin") - this only changes
# what HR sees on this page, not what group membership means.
ROLE_LABELS = {
    HR_ADMIN: 'Admin',
    RECRUITER: 'Recruiter',
    INTERVIEWER: 'Interviewer',
    HIRING_MANAGER: 'Hiring Manager',
}
ROLE_CHOICES = [(name, ROLE_LABELS[name]) for name in ALL_GROUPS]


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


class UserAccountForm(BootstrapFormMixin, forms.ModelForm):
    role = forms.ChoiceField(choices=ROLE_CHOICES, label='Role')
    password1 = forms.CharField(
        label='Password', required=False, widget=forms.PasswordInput,
        help_text="Leave both password fields blank to skip for now - the account just can't log in until "
                  'someone sets one (edit them again to add it later).')
    password2 = forms.CharField(label='Confirm password', required=False, widget=forms.PasswordInput)

    class Meta:
        model = User
        fields = ['username', 'first_name', 'last_name', 'email', 'is_active']
        labels = {'username': 'Login ID', 'is_active': 'Active (can log in)'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            current_role = self.instance.groups.filter(name__in=ALL_GROUPS).first()
            self.fields['role'].initial = current_role.name if current_role else None
            self.fields['password1'].label = 'New password'
            self.fields['password1'].help_text = 'Leave blank to keep the current password.'
        else:
            self.fields['is_active'].initial = True
        self._add_bootstrap_classes()

    def clean(self):
        cleaned = super().clean()
        password1, password2 = cleaned.get('password1'), cleaned.get('password2')
        if password1 or password2:
            if password1 != password2:
                self.add_error('password2', "Passwords don't match.")
            else:
                try:
                    validate_password(password1, self.instance)
                except ValidationError as exc:
                    self.add_error('password1', exc)
        return cleaned

    def save(self, commit=True):
        user = super().save(commit=False)
        password = self.cleaned_data.get('password1')
        if password:
            user.set_password(password)
        elif not user.pk:
            # New account, no password given (the SKIP option) - usable once
            # someone comes back and sets one, not silently logged-in-able.
            user.set_unusable_password()
        if commit:
            user.save()
            role_name = self.cleaned_data['role']
            user.groups.set([Group.objects.get_or_create(name=role_name)[0]])
        return user
