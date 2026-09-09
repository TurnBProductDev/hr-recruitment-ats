"""A simpler, in-app replacement for Django admin's Users/Groups pages -
scoped to exactly what HR needs: add/edit an account and put it under one of
the app's 4 roles (candidates/permissions.py). The raw Django admin at
/admin/ still exists for everything else (Candidates, Jobs, Interviews, ...).
"""
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views import View
from django.views.generic import CreateView, ListView, UpdateView

from candidates.permissions import ALL_GROUPS, HR_ADMIN, GroupRequiredMixin

from .user_forms import ROLE_LABELS, UserAccountForm

User = get_user_model()


class UserListView(GroupRequiredMixin, ListView):
    model = User
    template_name = 'dashboard/user_list.html'
    context_object_name = 'accounts'
    allowed_groups = (HR_ADMIN,)

    def get_queryset(self):
        return (User.objects.filter(groups__name__in=ALL_GROUPS).distinct()
                .prefetch_related('groups').order_by('first_name', 'last_name', 'username'))

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        for account in ctx['accounts']:
            role = account.groups.filter(name__in=ALL_GROUPS).first()
            account.role_label = ROLE_LABELS.get(role.name) if role else None
            account.has_password = account.has_usable_password()
        return ctx


class UserCreateView(GroupRequiredMixin, CreateView):
    model = User
    form_class = UserAccountForm
    template_name = 'dashboard/user_form.html'
    allowed_groups = (HR_ADMIN,)

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, f'{self.object.get_full_name() or self.object.username} was added.')
        return response

    def get_success_url(self):
        return reverse('user_list')


LAST_ADMIN_MESSAGE = 'This is the only active Admin account - promote someone else to Admin first.'


def _other_active_admins_exist(exclude_pk):
    return User.objects.filter(groups__name=HR_ADMIN, is_active=True).exclude(pk=exclude_pk).exists()


class UserUpdateView(GroupRequiredMixin, UpdateView):
    model = User
    form_class = UserAccountForm
    template_name = 'dashboard/user_form.html'
    allowed_groups = (HR_ADMIN,)

    def get_object(self, queryset=None):
        obj = super().get_object(queryset)
        # Snapshot before the form's validation overwrites is_active on this
        # same instance - the only way to know what it used to be, whether
        # this is a self-edit or someone editing another admin.
        self.was_active_admin = obj.is_active and obj.groups.filter(name=HR_ADMIN).exists()
        return obj

    def form_valid(self, form):
        # The one HR-wide safety rail on this page: a role/active change is
        # free otherwise (including on your own account), as long as at
        # least one other active Admin is left standing to fix things.
        still_active_admin = form.cleaned_data['is_active'] and form.cleaned_data['role'] == HR_ADMIN
        if self.was_active_admin and not still_active_admin and not _other_active_admins_exist(form.instance.pk):
            if not form.cleaned_data['is_active']:
                form.add_error('is_active', LAST_ADMIN_MESSAGE)
            if form.cleaned_data['role'] != HR_ADMIN:
                form.add_error('role', LAST_ADMIN_MESSAGE)
            return self.form_invalid(form)
        response = super().form_valid(form)
        messages.success(self.request, f'{self.object.get_full_name() or self.object.username} was updated.')
        return response

    def get_success_url(self):
        return reverse('user_list')


class UserToggleActiveView(GroupRequiredMixin, View):
    """One-click Deactivate/Activate from the list, for when a full Edit
    isn't needed - a departed employee's access can be pulled in one click."""
    allowed_groups = (HR_ADMIN,)

    def post(self, request, pk):
        account = get_object_or_404(User, pk=pk)
        is_active_admin = account.is_active and account.groups.filter(name=HR_ADMIN).exists()
        if is_active_admin and not _other_active_admins_exist(account.pk):
            messages.error(request, LAST_ADMIN_MESSAGE)
        else:
            account.is_active = not account.is_active
            account.save(update_fields=['is_active'])
            messages.success(
                request, f'{account.get_full_name() or account.username} is now '
                         f'{"active" if account.is_active else "deactivated"}.')
        return redirect('user_list')
