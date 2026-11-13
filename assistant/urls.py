from django.urls import path

from . import views

urlpatterns = [
    path('hr/assistant/ask/', views.AskView.as_view(), name='assistant_ask'),
]
