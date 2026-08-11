from django.urls import path

from . import views
from .models import Candidate

STATUS = Candidate.Status

urlpatterns = [
    # Public
    path('careers/<str:job_code>/apply/', views.ApplicationCreateView.as_view(), name='application_create'),
    path('careers/thank-you/<str:candidate_code>/', views.ApplicationThankYouView.as_view(), name='application_thank_you'),

    # HR admin: Candidate Repository
    path('hr/candidates/', views.CandidateRepositoryListView.as_view(), name='candidate_repository'),
    path('hr/candidates/all/', views.AllCandidatesListView.as_view(), name='candidate_all'),
    path('hr/candidates/general-applications/', views.GeneralApplicationsListView.as_view(),
         name='candidate_general_applications'),
    path('hr/candidates/delete-selected/', views.CandidateBulkDeleteView.as_view(),
         name='candidate_bulk_delete'),
    path('hr/candidates/reject-selected/', views.CandidateBulkRejectView.as_view(),
         name='candidate_bulk_reject'),
    path('hr/candidates/<int:pk>/', views.CandidateTimelineView.as_view(), name='candidate_timeline'),
    path('hr/candidates/<int:pk>/edit/', views.CandidateUpdateView.as_view(), name='candidate_edit'),
    path('hr/candidates/<int:pk>/change-job/', views.CandidateChangeJobView.as_view(), name='candidate_change_job'),
    path('hr/candidates/<int:pk>/change-source/', views.CandidateChangeSourceView.as_view(), name='candidate_change_source'),
    path('hr/candidates/<int:pk>/set-status/', views.CandidateSetStatusView.as_view(), name='candidate_set_status'),
    path('hr/candidates/<int:pk>/note/', views.AddNoteView.as_view(), name='candidate_add_note'),
    path('hr/candidates/<int:pk>/log/', views.AddCommunicationLogView.as_view(), name='candidate_add_log'),

    path('hr/candidates/<int:pk>/shortlist/', views.CandidateStatusActionView.as_view(
        target_status=STATUS.SHORTLISTED), name='candidate_shortlist'),
    path('hr/candidates/<int:pk>/round1/', views.CandidateStatusActionView.as_view(
        target_status=STATUS.ROUND1), name='candidate_round1'),
    path('hr/candidates/<int:pk>/interview/', views.CandidateStatusActionView.as_view(
        target_status=STATUS.INTERVIEW), name='candidate_interview_stage'),
    path('hr/candidates/<int:pk>/final-selection/', views.CandidateStatusActionView.as_view(
        target_status=STATUS.FINAL_SELECTION), name='candidate_final_selection'),
    path('hr/candidates/<int:pk>/hire/', views.CandidateStatusActionView.as_view(
        target_status=STATUS.HIRED), name='candidate_hire'),
    path('hr/candidates/<int:pk>/reject/', views.CandidateStatusActionView.as_view(
        target_status=STATUS.REJECTED, require_reason=False), name='candidate_reject'),
    path('hr/candidates/<int:pk>/blacklist/', views.CandidateStatusActionView.as_view(
        target_status=STATUS.BLACKLISTED, require_reason=True), name='candidate_blacklist'),
    path('hr/candidates/<int:pk>/screening-hold/', views.CandidateStatusActionView.as_view(
        target_status=STATUS.SCREENING_HOLD), name='candidate_screening_hold'),

    path('hr/candidates/<int:pk>/revert/', views.CandidateRevertLastActionView.as_view(), name='candidate_revert'),
    path('hr/candidates/<int:pk>/delete/', views.CandidateDeleteView.as_view(), name='candidate_delete'),
    path('hr/candidates/reject-closed-vacancies/', views.BulkRejectClosedVacanciesView.as_view(),
         name='candidate_reject_closed'),

    path('hr/candidates/bulk-upload/', views.BulkUploadCVView.as_view(), name='candidate_bulk_upload'),
    path('hr/candidates/bulk-upload/<int:pk>/', views.BulkUploadProgressView.as_view(),
         name='candidate_bulk_progress'),
    path('hr/candidates/bulk-upload/<int:pk>/status/', views.BulkUploadStatusView.as_view(),
         name='candidate_bulk_status'),
    path('hr/candidates/bulk-upload/<int:pk>/retry/', views.BulkUploadRetryView.as_view(),
         name='candidate_bulk_retry'),
]
