"""Every login-account change is recorded with who made it (dashboard/user_audit.py).

    DB_ENGINE=sqlite python manage.py test dashboard.test_user_audit
"""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from candidates.permissions import HIRING_MANAGER, HR_ADMIN, INTERVIEWER

from .models import AccountChangeLog

User = get_user_model()


class AccountAuditTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user('dilshad', 'd@turnb.com', 'pw', first_name='Dilshad', last_name='MN')
        self.admin.groups.add(Group.objects.get_or_create(name=HR_ADMIN)[0])
        self.subha = User.objects.create_user('subha', 's@turnb.com', 'pw', first_name='Subha', last_name='V Menon')
        self.subha.groups.add(Group.objects.get_or_create(name=INTERVIEWER)[0])
        AccountChangeLog.objects.all().delete()  # setUp's own creations ran outside a request
        self.client.force_login(self.admin)

    def _edit(self, **overrides):
        data = {'username': 'subha', 'first_name': 'Subha', 'last_name': 'V Menon', 'email': 's@turnb.com',
                'is_active': 'on', 'role': INTERVIEWER, 'password1': '', 'password2': ''}
        data.update(overrides)
        return self.client.post(reverse('user_edit', args=[self.subha.pk]), data)

    def test_role_change_on_users_page_is_one_entry_with_who_did_it(self):
        self._edit(role=HIRING_MANAGER)
        entry = AccountChangeLog.objects.get()
        self.assertEqual(entry.account, self.subha)
        self.assertEqual(entry.changed_by, self.admin)
        self.assertEqual(entry.changed_by_name, 'Dilshad MN')
        self.assertEqual(entry.summary, 'Role: Interviewer → Hiring Manager')

    def test_several_changes_in_one_save_are_listed_together(self):
        self._edit(role=HIRING_MANAGER, email='subha.menon@turnb.com', is_active='')
        summary = AccountChangeLog.objects.get().summary
        self.assertIn('Email: s@turnb.com → subha.menon@turnb.com', summary)
        self.assertIn('Active: Yes → No', summary)
        self.assertIn('Role: Interviewer → Hiring Manager', summary)

    def test_password_change_is_recorded_without_the_password(self):
        self._edit(password1='N3w-Secret!pass', password2='N3w-Secret!pass')
        entry = AccountChangeLog.objects.get()
        self.assertEqual(entry.summary, 'Password changed')
        self.assertNotIn('N3w', entry.summary)

    def test_saving_without_changes_records_nothing(self):
        self._edit()
        self.assertFalse(AccountChangeLog.objects.exists())

    def test_one_click_deactivate_is_recorded(self):
        self.client.post(reverse('user_toggle_active', args=[self.subha.pk]))
        self.assertEqual(AccountChangeLog.objects.get().summary, 'Active: Yes → No')

    def test_new_account_is_recorded_with_its_role(self):
        self.client.post(reverse('user_add'), {
            'username': 'newbie', 'first_name': 'New', 'last_name': 'Person', 'email': 'n@turnb.com',
            'is_active': 'on', 'role': INTERVIEWER, 'password1': '', 'password2': ''})
        entry = AccountChangeLog.objects.get()
        self.assertEqual(entry.account_name, 'New Person')
        self.assertEqual(entry.summary, 'Account created; Role: Interviewer')

    def test_own_password_change_says_themself(self):
        self.client.force_login(self.subha)
        self.client.post(reverse('password_change'), {
            'old_password': 'pw', 'new_password1': 'An0ther-Secret!x', 'new_password2': 'An0ther-Secret!x'})
        entry = AccountChangeLog.objects.get()
        self.assertEqual(entry.summary, 'Password changed')
        self.assertEqual(entry.changed_by_name, 'Subha V Menon (themself)')

    def test_signing_in_is_not_an_account_change(self):
        self.client.logout()
        self.client.post(reverse('login'), {'username': 'subha', 'password': 'pw'})
        self.assertFalse(AccountChangeLog.objects.exists())

    def test_django_admin_change_is_recorded(self):
        self.admin.is_staff = self.admin.is_superuser = True
        self.admin.save()
        AccountChangeLog.objects.all().delete()
        self.client.force_login(self.admin)
        self.client.post(reverse('admin:auth_user_change', args=[self.subha.pk]), {
            'username': 'subha', 'first_name': 'Subha', 'last_name': 'V Menon', 'email': 's@turnb.com',
            'is_active': 'on', 'groups': [Group.objects.get_or_create(name=HIRING_MANAGER)[0].pk],
            'date_joined_0': '2026-01-01', 'date_joined_1': '00:00:00',
            'initial-date_joined_0': '2026-01-01', 'initial-date_joined_1': '00:00:00',
        })
        entry = AccountChangeLog.objects.get(account=self.subha)
        self.assertIn('Role: Interviewer → Hiring Manager', entry.summary)
        self.assertEqual(entry.changed_by, self.admin)

    def test_history_page_lists_entries_per_account(self):
        self._edit(role=HIRING_MANAGER)
        response = self.client.get(reverse('user_history'), {'account': self.subha.pk})
        self.assertContains(response, 'History: Subha V Menon')
        self.assertContains(response, 'Role: Interviewer → Hiring Manager')
        self.assertContains(response, 'Dilshad MN')
        self.assertContains(self.client.get(reverse('user_list')), reverse('user_history'))
