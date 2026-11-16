from django.urls import path

from . import views

urlpatterns = [
    path('hr/chatbot/ask/', views.AskView.as_view(), name='chatbot_ask'),
]
