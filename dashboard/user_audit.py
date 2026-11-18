"""Records every change to a login account in AccountChangeLog.

Hooked on the User model itself (signals), not on any one page, so nothing
slips past: the in-app Users page, its one-click Activate/Deactivate, the
Django admin, a user changing their own password, or a password-reset link.

Who made the change comes from the request in progress (AccountAuditMiddleware
keeps it for the duration of the request). Changes made during one request -
e.g. saving the Edit User form updates the account row AND swaps its role
group - are collected and written as one entry per account when the request
finishes, so it reads "Role: Interviewer -> Hiring Manager; Active: Yes -> No"
rather than as several fragments. Outside a request (shell, scripts) each
change is written straight away, attributed to "System".
"""
import contextvars

from django.contrib.auth import get_user_model
from django.db.models.signals import m2m_changed, post_delete, post_save, pre_save
from django.dispatch import receiver

from candidates.permissions import ALL_GROUPS

User = get_user_model()

_request = contextvars.ContextVar('account_audit_request', default=None)
_pending = contextvars.ContextVar('account_audit_pending', default=None)

TRACKED = (
    ('username', 'Login ID'),
    ('first_name', 'First name'),
    ('last_name', 'Last name'),
    ('email', 'Email'),
    ('is_active', 'Active'),
    ('is_staff', 'Django admin access'),
    ('is_superuser', 'Superuser'),
)


def _name(user):
    return (user.get_full_name() or user.get_username()) if user else ''


def _show(value):
    if isinstance(value, bool):
        return 'Yes' if value else 'No'
    return value or '(blank)'


def _role_label(names):
    from .user_forms import ROLE_LABELS  # local: user_forms imports models
    shown = [ROLE_LABELS.get(n, n) for n in sorted(names)]
    return ', '.join(shown) or 'None'


def _entry(user):
    """The pending entry for this account in the current request (or a
    throwaway one outside a request, written immediately by _done)."""
    pending = _pending.get()
    if pending is None:
        return {'user': user, 'created': False, 'changes': [], 'roles_before': None, 'deleted': False}
    entry = pending.setdefault(user.pk, {'user': user, 'created': False, 'changes': [],
                                         'roles_before': None, 'deleted': False})
    entry['user'] = user
    return entry


def _done(entry):
    if _pending.get() is None:
        _write(entry)


def _roles(user):
    return set(user.groups.filter(name__in=ALL_GROUPS).values_list('name', flat=True))


def _write(entry):
    from .models import AccountChangeLog
    user = entry['user']
    parts = []
    if entry['deleted']:
        parts.append('Account deleted')
    elif entry['created']:
        parts.append('Account created')
    parts.extend(entry['changes'])
    if entry['roles_before'] is not None and not entry['deleted']:
        before, after = entry['roles_before'], _roles(user)
        if before != after:
            parts.append(f'Role: {_role_label(after)}' if entry['created']
                         else f'Role: {_role_label(before)} → {_role_label(after)}')
    if not parts:
        return
    request = _request.get()
    actor = getattr(request, 'user', None) if request is not None else None
    if actor is not None and actor.is_authenticated:
        changed_by, changed_by_name = actor, _name(actor)
        if actor.pk == user.pk:
            changed_by_name += ' (themself)'
    elif request is not None:
        # Not signed in - only the password-reset link changes an account this way.
        changed_by, changed_by_name = None, 'Password reset link (the account holder)'
    else:
        changed_by, changed_by_name = None, 'System'
    AccountChangeLog.objects.create(
        account=None if entry['deleted'] else user, account_name=_name(user),
        changed_by=changed_by, changed_by_name=changed_by_name, summary='; '.join(parts))


@receiver(pre_save, sender=User)
def _snapshot(sender, instance, update_fields=None, raw=False, **kwargs):
    instance._audit_before = None
    if raw or not instance.pk or (update_fields and set(update_fields) <= {'last_login', 'password'}):
        # Sign-in only stamps last_login, and may silently re-hash the same
        # password with a stronger algorithm (save(update_fields=['password'])) -
        # neither is an account change. Real password changes (own change,
        # reset link, Users page, admin) save the whole row.
        return
    instance._audit_before = User.objects.filter(pk=instance.pk).values(
        'password', *(f for f, _ in TRACKED)).first()


@receiver(post_save, sender=User)
def _saved(sender, instance, created, raw=False, **kwargs):
    if raw:
        return
    if created:
        entry = _entry(instance)
        entry['created'] = True
        entry['roles_before'] = set() if entry['roles_before'] is None else entry['roles_before']
        if instance.has_usable_password():
            entry['changes'].append('Password set')
        _done(entry)
        return
    before = getattr(instance, '_audit_before', None)
    if not before:
        return
    changes = [f'{label}: {_show(before[f])} → {_show(getattr(instance, f))}'
               for f, label in TRACKED if before[f] != getattr(instance, f)]
    if before['password'] != instance.password:
        changes.append('Password changed' if instance.has_usable_password() else 'Password removed')
    if changes:
        entry = _entry(instance)
        entry['changes'].extend(c for c in changes if c not in entry['changes'])
        _done(entry)


@receiver(m2m_changed, sender=User.groups.through)
def _groups_changed(sender, instance, action, reverse, pk_set=None, **kwargs):
    if reverse or not isinstance(instance, User):
        return  # changed from the Group side (admin Group page) - rare; skip
    if action in ('pre_add', 'pre_remove', 'pre_clear'):
        entry = _entry(instance)
        if entry['roles_before'] is None:
            entry['roles_before'] = _roles(instance)
        instance._audit_group_entry = entry
    elif action in ('post_add', 'post_remove', 'post_clear'):
        entry = getattr(instance, '_audit_group_entry', None) or _entry(instance)
        _done(entry)


@receiver(post_delete, sender=User)
def _deleted(sender, instance, **kwargs):
    entry = _entry(instance)
    entry['deleted'] = True
    _done(entry)


class AccountAuditMiddleware:
    """Remembers who's making the request, and writes the account changes it
    collected once the response is ready."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request_token = _request.set(request)
        pending_token = _pending.set({})
        try:
            response = self.get_response(request)
        finally:
            pending = _pending.get()
            _pending.reset(pending_token)
            try:
                for entry in pending.values():
                    try:
                        _write(entry)
                    except Exception:  # noqa: BLE001 - never break the page over the audit log
                        import logging
                        logging.getLogger(__name__).exception('Could not write the account change log')
            finally:
                _request.reset(request_token)
        return response
