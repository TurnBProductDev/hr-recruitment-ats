"""Run against sqlite so the live Azure DB is never touched:
    DB_ENGINE=sqlite python manage.py test notifications
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Notification


class NotificationListViewTests(TestCase):
    """The Notifications page is a running feed of what's recent, not an
    archive - always just the last 7, however many exist in total."""

    def setUp(self):
        self.user = get_user_model().objects.create_user('hr', 'hr@example.com', 'pw')
        self.client.force_login(self.user)

    def _make(self, n, **kwargs):
        notifications = [
            Notification(recipient=self.user, title=f'Notification {i}', **kwargs)
            for i in range(n)]
        Notification.objects.bulk_create(notifications)
        # bulk_create bypasses auto_now_add, so stagger created_at explicitly -
        # newest last (index n-1), matching arrival order.
        now = timezone.now()
        for i, notif in enumerate(Notification.objects.filter(recipient=self.user).order_by('pk')):
            notif.created_at = now - timedelta(minutes=(n - i))
            notif.save(update_fields=['created_at'])

    def test_shows_at_most_seven(self):
        self._make(12)
        response = self.client.get(reverse('notification_list'))
        self.assertEqual(len(response.context['notifications']), 7)

    def test_shows_the_seven_most_recent(self):
        self._make(10)
        response = self.client.get(reverse('notification_list'))
        titles = [n.title for n in response.context['notifications']]
        self.assertEqual(titles, [f'Notification {i}' for i in range(9, 2, -1)])

    def test_fewer_than_seven_all_show(self):
        self._make(3)
        response = self.client.get(reverse('notification_list'))
        self.assertEqual(len(response.context['notifications']), 3)
