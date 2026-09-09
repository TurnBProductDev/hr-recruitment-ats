from django.urls import path

from . import portal_views, views

urlpatterns = [
    path('hr/interviews/', views.InterviewSchedulerListView.as_view(), name='interview_scheduler'),
    path('hr/candidates/<int:candidate_id>/interviews/schedule/', views.InterviewScheduleView.as_view(), name='interview_schedule'),
    path('hr/candidates/<int:candidate_id>/interviews/allocate/', views.InterviewAllocateView.as_view(), name='interview_allocate'),
    path('hr/interviews/<int:pk>/reschedule/', views.InterviewRescheduleView.as_view(), name='interview_reschedule'),
    path('hr/interviews/<int:pk>/mark-done/', views.InterviewMarkDoneView.as_view(), name='interview_mark_done'),
    path('hr/interviews/<int:pk>/cancel/', views.InterviewCancelView.as_view(), name='interview_cancel'),
    path('hr/interviews/<int:pk>/result/', views.InterviewResultView.as_view(), name='interview_result'),
    path('hr/interviews/<int:pk>/send-invite/', views.InterviewSendInviteView.as_view(), name='interview_send_invite'),
    path('hr/interview-requests/<int:pk>/select-slot/', views.InterviewSelectSlotView.as_view(), name='interview_request_select_slot'),
    path('hr/interview-requests/<int:pk>/new-slots/', views.InterviewRequestNewSlotsView.as_view(), name='interview_request_new_slots'),

    path('interviewer/login/', portal_views.InterviewerLoginView.as_view(), name='interviewer_login'),
    path('interviewer/', portal_views.InterviewerHomeView.as_view(), name='interviewer_home'),
    path('interviewer/candidates/<int:pk>/', portal_views.InterviewerCandidateView.as_view(), name='interviewer_candidate'),
    path('interviewer/requests/<int:pk>/propose-slots/', portal_views.InterviewProposeSlotsView.as_view(), name='interviewer_propose_slots'),
]
