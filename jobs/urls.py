from django.urls import path

from . import views

urlpatterns = [
    # Public
    path('careers/', views.VacancyListView.as_view(), name='vacancy_list'),
    path('careers/<str:job_code>/', views.VacancyDetailView.as_view(), name='vacancy_detail'),

    # HR admin: Vacancy Management
    path('hr/jobs/', views.JobManageListView.as_view(), name='job_manage_list'),
    path('hr/jobs/add/', views.JobCreateView.as_view(), name='job_add'),
    path('hr/jobs/extract-jd/', views.JobExtractJDView.as_view(), name='job_extract_jd'),
    path('hr/jobs/<int:pk>/edit/', views.JobUpdateView.as_view(), name='job_edit'),
    path('hr/jobs/<int:pk>/close/', views.JobCloseView.as_view(), name='job_close'),
    path('hr/jobs/<int:pk>/archive/', views.JobArchiveView.as_view(), name='job_archive'),
    path('hr/jobs/<int:pk>/reopen/', views.JobReopenView.as_view(), name='job_reopen'),
]
