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


class UserUpdateView(GroupRequiredMixin, UpdateView):
    model = User
    form_class = UserAccountForm
    template_name = 'dashboard/user_form.html'
    allowed_groups = (HR_ADMIN,)

    def form_valid(self, form):
        # An admin can't lock themselves out - deactivating their own account
        # or demoting themselves out of Admin would leave nobody able to fix it.
        if form.instance.pk == self.request.user.pk:
            if not form.cleaned_data['is_active']:
                form.add_error('is_active', "You can't deactivate your own account.")
                return self.form_invalid(form)
            if form.cleaned_data['role'] != HR_ADMIN:
                form.add_error('role', "You can't remove your own Admin role.")
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
        if account.pk == request.user.pk:
            messages.error(request, "You can't deactivate your own account.")
        else:
            account.is_active = not account.is_active
            account.save(update_fields=['is_active'])
            messages.success(
                request, f'{account.get_full_name() or account.username} is now '
                         f'{"active" if account.is_active else "deactivated"}.')
        return redirect('user_list')
