from django.urls import path

from . import views

urlpatterns = [
    path('hr/dashboard/', views.HRDashboardView.as_view(), name='hr_dashboard'),
    path('hr/dashboard/daily/<str:column>/', views.DailyActionDrilldownView.as_view(), name='daily_action_drilldown'),
    path('hr/reports/', views.ReportsView.as_view(), name='hr_reports'),
]
