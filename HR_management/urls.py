"""
URL configuration for HR_management project.
"""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.auth.decorators import login_required
from django.urls import include, path
from django.views.generic import TemplateView
from django.views.static import serve as serve_static_file

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
    # Uploaded files (Job.jd_file, Candidate Attachments, ...) - served here in
    # every environment, not just DEBUG. django.contrib.staticfiles/WhiteNoise
    # only ever covers STATIC_URL; nothing served MEDIA_URL in production
    # before this, so any template linking straight at a FileField's .url
    # (job_form.html, job_manage_list.html, the candidate Attachments list)
    # 404'd there even though the file exists at MEDIA_ROOT. Gated behind
    # login - candidate attachments in particular are not meant to be
    # reachable by anyone who finds/guesses the URL.
    path(f'{settings.MEDIA_URL.lstrip("/")}<path:path>',
         login_required(serve_static_file), {'document_root': settings.MEDIA_ROOT}),
]

if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
