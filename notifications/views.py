from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.views import View
from django.views.generic import ListView

from .models import Notification


class NotificationListView(LoginRequiredMixin, ListView):
    model = Notification
    template_name = 'notifications/list.html'
    context_object_name = 'notifications'
    paginate_by = 30

    def get_queryset(self):
        return self.request.user.notifications.all()


class NotificationMarkReadView(LoginRequiredMixin, View):
    """Marks one notification read, then sends the user on to whatever it was
    about - this is what the bell dropdown's rows and the list page both
    post to."""

    def post(self, request, pk):
        notification = get_object_or_404(Notification, pk=pk, recipient=request.user)
        if not notification.is_read:
            notification.is_read = True
            notification.read_at = timezone.now()
            notification.save(update_fields=['is_read', 'read_at'])
        return redirect(notification.url or 'notification_list')


class NotificationMarkAllReadView(LoginRequiredMixin, View):
    def post(self, request):
        request.user.notifications.filter(is_read=False).update(is_read=True, read_at=timezone.now())
        return redirect(request.POST.get('next') or 'notification_list')
