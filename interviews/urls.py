from django.urls import path

from . import views

urlpatterns = [
    path('hr/interviews/', views.InterviewSchedulerListView.as_view(), name='interview_scheduler'),
    path('hr/candidates/<int:candidate_id>/interviews/schedule/', views.InterviewScheduleView.as_view(), name='interview_schedule'),
    path('hr/interviews/<int:pk>/reschedule/', views.InterviewRescheduleView.as_view(), name='interview_reschedule'),
    path('hr/interviews/<int:pk>/mark-done/', views.InterviewMarkDoneView.as_view(), name='interview_mark_done'),
    path('hr/interviews/<int:pk>/cancel/', views.InterviewCancelView.as_view(), name='interview_cancel'),
    path('hr/interviews/<int:pk>/result/', views.InterviewResultView.as_view(), name='interview_result'),
    path('hr/interviews/<int:pk>/send-invite/', views.InterviewSendInviteView.as_view(), name='interview_send_invite'),
]
