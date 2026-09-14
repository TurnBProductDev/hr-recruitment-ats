"""
URL configuration for HR_management project.
"""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.urls import include, path
from django.views.decorators.clickjacking import xframe_options_exempt
from django.views.generic import TemplateView
from django.views.static import serve as serve_static_file

from .auth_views import HRLoginView, HRLogoutView, HRPasswordChangeView
from .password_forms import BootstrapSetPasswordForm, LogicAppPasswordResetForm

urlpatterns = [
    path('admin/', admin.site.urls),
    path('login/', HRLoginView.as_view(template_name='registration/login.html'), name='login'),
    path('logout/', HRLogoutView.as_view(), name='logout'),
    path('password/change/', HRPasswordChangeView.as_view(), name='password_change'),
    path('password/reset/', auth_views.PasswordResetView.as_view(
        template_name='registration/password_reset_form.html',
        form_class=LogicAppPasswordResetForm,
        success_url='/password/reset/done/',
    ), name='password_reset'),
    path('password/reset/done/', auth_views.PasswordResetDoneView.as_view(
        template_name='registration/password_reset_done.html',
    ), name='password_reset_done'),
    path('password/reset/confirm/<uidb64>/<token>/', auth_views.PasswordResetConfirmView.as_view(
        template_name='registration/password_reset_confirm.html',
        form_class=BootstrapSetPasswordForm,
        success_url='/password/reset/complete/',
    ), name='password_reset_confirm'),
    path('password/reset/complete/', auth_views.PasswordResetCompleteView.as_view(
        template_name='registration/password_reset_complete.html',
    ), name='password_reset_complete'),
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
    # reachable by anyone who finds/guesses the URL. xframe_options_exempt
    # because the Vacancy edit page previews the JD in an <iframe> the same
    # way CandidateCvView does for CVs - XFrameOptionsMiddleware's default
    # X-Frame-Options: DENY would otherwise block that on the very first
    # response, same trap CandidateCvView's docstring already explains.
    path(f'{settings.MEDIA_URL.lstrip("/")}<path:path>',
         xframe_options_exempt(login_required(serve_static_file)), {'document_root': settings.MEDIA_ROOT}),
]

if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
