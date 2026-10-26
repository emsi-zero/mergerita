import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

DEBUG = os.environ.get("DEBUG", "0") == "1"
SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-secret-key" if DEBUG else "")
if not SECRET_KEY:
    raise RuntimeError("SECRET_KEY must be set when DEBUG is off.")
ALLOWED_HOSTS = os.environ.get("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "rest_framework",
    "integrations",
    "audit",
    "reviews",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]

ROOT_URLCONF = "orchestrator.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "HOST": os.environ.get("DB_HOST", "localhost"),
        "PORT": os.environ.get("DB_PORT", "5432"),
        "NAME": os.environ.get("DB_NAME", "orchestrator"),
        "USER": os.environ.get("DB_USER", "orchestrator"),
        "PASSWORD": os.environ.get("DB_PASSWORD", "orchestrator"),
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True
TIME_ZONE = "UTC"
STATIC_URL = "static/"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAdminUser"],
}

GITLAB_WEBHOOK_TOKEN = os.environ.get("GITLAB_WEBHOOK_TOKEN", "")

# Review rules.
RMO_SLOT_MINUTES = 60
RMO_DEFAULT_DAILY_BUDGET_MINUTES = 120
RMO_BOOKING_HORIZON_DAYS = 10
RMO_AI_PASS_LABEL = "ai-review::passed"
RMO_AI_FAIL_LABEL = "ai-review::failed"
RMO_COVERAGE_POLICY = "strict"  # "strict" or "combined"
# Weekday numbers follow datetime.weekday(): Monday is 0. Saturday to Wednesday.
RMO_DEFAULT_WORKING_DAYS = [5, 6, 0, 1, 2]

# Inbox. A delivery younger than this is not claimed yet, so an earlier
# delivery still committing is not overtaken (RMO-6).
RMO_INBOX_CLAIM_GRACE_SECONDS = 2

# Outbox.
RMO_OUTBOX_MAX_ATTEMPTS = 5
RMO_OUTBOX_BACKOFF_SECONDS = 30
