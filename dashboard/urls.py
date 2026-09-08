from django.urls import path

from . import user_views, views

urlpatterns = [
    path('hr/dashboard/', views.HRDashboardView.as_view(), name='hr_dashboard'),
    path('hr/dashboard/daily/<str:column>/', views.DailyActionDrilldownView.as_view(), name='daily_action_drilldown'),
    path('hr/reports/', views.ReportsView.as_view(), name='hr_reports'),

    path('hr/users/', user_views.UserListView.as_view(), name='user_list'),
    path('hr/users/add/', user_views.UserCreateView.as_view(), name='user_add'),
    path('hr/users/<int:pk>/edit/', user_views.UserUpdateView.as_view(), name='user_edit'),
    path('hr/users/<int:pk>/toggle-active/', user_views.UserToggleActiveView.as_view(), name='user_toggle_active'),
]
