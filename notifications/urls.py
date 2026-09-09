from django.urls import path

from . import views

urlpatterns = [
    path('notifications/', views.NotificationListView.as_view(), name='notification_list'),
    path('notifications/<int:pk>/read/', views.NotificationMarkReadView.as_view(), name='notification_mark_read'),
    path('notifications/mark-all-read/', views.NotificationMarkAllReadView.as_view(), name='notification_mark_all_read'),
]
