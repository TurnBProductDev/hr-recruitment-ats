"""
Django settings for HR_management project.
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# Load environment variables from a local .env file (if present) so DB
# credentials etc. don't have to be set in the OS environment. Safe no-op
# in production where real environment variables are used instead.
from dotenv import load_dotenv
load_dotenv(BASE_DIR / '.env')

SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY",
    "django-insecure-af^fer9mep9gg^j6q_c(a%%63)&)5292q5ihgs&lm^7$g#xq)9",
)

DEBUG = os.environ.get("DJANGO_DEBUG", "True") == "True"

ALLOWED_HOSTS = [h.strip() for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "*").split(",") if h.strip()]

# Comma-separated https origins Django should trust for CSRF (your App Service URL),
# e.g. https://hr-ats.azurewebsites.net
CSRF_TRUSTED_ORIGINS = [o.strip() for o in os.environ.get("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",") if o.strip()]

# Azure App Service terminates TLS at the front end; trust the forwarded proto
# so Django knows the request is HTTPS (correct redirects, secure cookies).
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")


INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.humanize',
    'jobs',
    'candidates',
    'interviews',
    'dashboard',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',  # serves static files in production
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'HR_management.middleware.NoCacheForAuthenticatedMiddleware',
]

ROOT_URLCONF = 'HR_management.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'HR_management.wsgi.application'


# Database
# Defaults to SQLite so `migrate && runserver` works with zero setup.
# Set DB_ENGINE=postgres (+ DB_NAME/DB_USER/DB_PASSWORD/DB_HOST/DB_PORT) to
# switch to PostgreSQL, or use an Azure SQL compatible ODBC engine the same way.
DB_ENGINE = os.environ.get("DB_ENGINE", "sqlite")

if DB_ENGINE == "postgres":
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': os.environ.get("DB_NAME", "hr_recruitment"),
            'USER': os.environ.get("DB_USER", "postgres"),
            'PASSWORD': os.environ.get("DB_PASSWORD", ""),
            'HOST': os.environ.get("DB_HOST", "localhost"),
            'PORT': os.environ.get("DB_PORT", "5432"),
        }
    }
elif DB_ENGINE in ("mssql", "azure", "azuresql"):
    # Azure SQL Database (or any Microsoft SQL Server) via mssql-django + ODBC.
    # Connection Timeout is generous because a serverless free-tier database
    # can take ~30-60s to wake from auto-pause on the first request.
    DATABASES = {
        'default': {
            'ENGINE': 'mssql',
            'NAME': os.environ.get("DB_NAME", "hr_recruitment"),
            'USER': os.environ.get("DB_USER", ""),
            'PASSWORD': os.environ.get("DB_PASSWORD", ""),
            'HOST': os.environ.get("DB_HOST", ""),
            'PORT': os.environ.get("DB_PORT", "1433"),
            'OPTIONS': {
                'driver': os.environ.get("DB_ODBC_DRIVER", "ODBC Driver 18 for SQL Server"),
                # Generous login timeout so the first request can wait for the
                # serverless database to wake from auto-pause instead of erroring.
                'connection_timeout': 60,
                'extra_params': (
                    "Encrypt=yes;TrustServerCertificate=no;Connection Timeout=60;"
                ),
            },
        }
    }
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
        }
    }


AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]


LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'Asia/Kolkata'
USE_I18N = True
USE_TZ = True


# Static files (WhiteNoise serves them directly from the app in production)
STATIC_URL = 'static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}

# Media (CV uploads). On Azure App Service set MEDIA_ROOT=/home/media so uploads
# persist across restarts/deploys (the /home volume is persisted).
MEDIA_URL = 'media/'
MEDIA_ROOT = os.environ.get("MEDIA_ROOT", BASE_DIR / 'media')

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# --- Bulk Upload CV -> `cv-parse-single` Logic App -------------------------
# HTTP POST URL of the Logic App trigger that reads one CV (Form Recognizer +
# Azure OpenAI) and returns the extracted fields. Contains a SAS signature, so
# keep it in app settings / .env, never in the repo. Setup: logic_apps/README.md.
# Unset = bulk upload records every file as an error and creates no candidates.
LOGIC_APP_CV_PARSER_URL = os.environ.get("LOGIC_APP_CV_PARSER_URL", "")
# Seconds to wait for one CV (the workflow answers 202 + Location if it needs
# longer than the trigger's synchronous window, and the client polls that).
CV_PARSER_TIMEOUT = int(os.environ.get("CV_PARSER_TIMEOUT", "180"))
# Whether the Logic App also files the CV in the SharePoint "Resume Received"
# folder and returns a sharing link (stored on the candidate as resume_url).
CV_PARSER_UPLOAD_TO_SHAREPOINT = os.environ.get("CV_PARSER_UPLOAD_TO_SHAREPOINT", "True") == "True"
BULK_UPLOAD_MAX_FILES = int(os.environ.get("BULK_UPLOAD_MAX_FILES", "25"))
BULK_UPLOAD_MAX_MB = int(os.environ.get("BULK_UPLOAD_MAX_MB", "10"))

LOGIN_URL = 'login'
LOGIN_REDIRECT_URL = 'hr_dashboard'
LOGOUT_REDIRECT_URL = 'vacancy_list'

# Console backend by default so "Send Invite" / notifications work without
# any setup in dev. Point EMAIL_BACKEND at django.core.mail.backends.smtp
# and set EMAIL_HOST/PORT/HOST_USER/HOST_PASSWORD for real delivery.
EMAIL_BACKEND = os.environ.get(
    "EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend"
)
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "hr@example.com")
