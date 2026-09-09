"""
URL configuration for HR_management project.
"""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from django.views.generic import TemplateView

from .auth_views import HRLoginView, HRLogoutView

urlpatterns = [
    path('admin/', admin.site.urls),
    path('login/', HRLoginView.as_view(template_name='registration/login.html'), name='login'),
    path('logout/', HRLogoutView.as_view(), name='logout'),
    path('', TemplateView.as_view(template_name='landing.html'), name='landing'),
    path('', include('jobs.urls')),
    path('', include('candidates.urls')),
    path('', include('interviews.urls')),
    path('', include('dashboard.urls')),
    path('', include('notifications.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
