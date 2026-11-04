import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

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
    "django.contrib.staticfiles",
    "rest_framework",
    "integrations",
    "audit",
    "reviews",
    "panel",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "panel.middleware.AreaSessionMiddleware",
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
TIME_ZONE = "Asia/Tehran"
STATIC_URL = "static/"
LOGIN_URL = "/panel/login/"
PANEL_SESSION_COOKIE_NAME = "panel_sessionid"
LOGIN_REDIRECT_URL = "/panel/"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAdminUser"],
}

GITLAB_WEBHOOK_TOKEN = os.environ.get("GITLAB_WEBHOOK_TOKEN", "")
GITLAB_URL = os.environ.get("GITLAB_URL", "")
GITLAB_TOKEN = os.environ.get("GITLAB_TOKEN", "")

# Review rules.
RMO_SLOT_MINUTES = 60
RMO_DEFAULT_DAILY_BUDGET_MINUTES = 120
RMO_BOOKING_HORIZON_DAYS = 10
RMO_DAILY_BUDGET_CHOICES = [60, 120, 180, 240]
RMO_PRESENCE_NOTE_MAX_LENGTH = 500
# GitLab user id of the AI Reviewer bot. No default: the AI-review check raises when unset.
_AI_REVIEWER_USER_ID = os.environ.get("RMO_AI_REVIEWER_GITLAB_USER_ID")
RMO_AI_REVIEWER_GITLAB_USER_ID = (
    int(_AI_REVIEWER_USER_ID) if _AI_REVIEWER_USER_ID else None
)
# GitLab project ids that reconciliation reads, comma-separated. Empty turns it off.
RMO_GITLAB_PROJECT_IDS = [
    int(project_id)
    for project_id in os.environ.get("RMO_GITLAB_PROJECT_IDS", "").split(",")
    if project_id.strip()
]
RMO_RECONCILE_INTERVAL_SECONDS = 300
# Each list call asks for MRs updated since the cursor minus this lag, so an MR whose
# update was not visible yet when the cursor passed it is read on the next call.
RMO_RECONCILE_LAG_SECONDS = 60
RMO_COVERAGE_POLICY = "strict"  # "strict" or "combined"
# Weekday numbers follow datetime.weekday(): Monday is 0. Saturday to Wednesday.
RMO_DEFAULT_WORKING_DAYS = [5, 6, 0, 1, 2]

# Inbox. A delivery younger than this is not claimed yet, so an earlier
# delivery still committing is not overtaken (RMO-6).
RMO_INBOX_CLAIM_GRACE_SECONDS = 2

# Outbox.
RMO_OUTBOX_MAX_ATTEMPTS = 5
RMO_OUTBOX_BACKOFF_SECONDS = 30
RMO_OUTBOX_LEASE_SECONDS = 60

# Adapters, as dotted paths to classes. The prototype points at the fakes.
RMO_GITLAB_TIMEOUT_SECONDS = 10  # below RMO_OUTBOX_LEASE_SECONDS
# Whole-call deadline of one gateway method. The deadline plus one request timeout
# (RMO_GITLAB_TIMEOUT_SECONDS) must stay below RMO_OUTBOX_LEASE_SECONDS.
RMO_GITLAB_CALL_DEADLINE_SECONDS = 45
RMO_GITLAB_GATEWAY = os.environ.get(
    "RMO_GITLAB_GATEWAY", "integrations.fakes.FakeGitLabGateway"
)
RMO_NOTIFIER = "integrations.fakes.FakeNotifier"


PANEL_AUTH_MODE_CHOICES = ("local", "sso")


def parse_panel_auth_modes(raw: str) -> tuple[str, ...]:
    """Return the enabled panel sign-in modes from a comma-separated string."""
    modes = tuple(mode.strip() for mode in raw.split(","))
    unknown = [mode for mode in modes if mode not in PANEL_AUTH_MODE_CHOICES]
    if unknown:
        raise ImproperlyConfigured(
            f"PANEL_AUTH_MODES must list only {PANEL_AUTH_MODE_CHOICES}, "
            f"not empty or unknown values: {unknown!r}"
        )
    return modes


PANEL_AUTH_MODES = parse_panel_auth_modes(
    os.environ.get("PANEL_AUTH_MODES", "local,sso")
)
