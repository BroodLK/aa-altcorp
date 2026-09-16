SECRET_KEY = "aa-altcorp-test-key"
DEBUG = False
USE_TZ = True
ROOT_URLCONF = "aa_altcorp.urls"
STATIC_URL = "/static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
# django.contrib.admin is included because the alert engine is administered
# entirely through it. allianceauth, aa_contacts and aadiscordbot stay absent on
# purpose: the plugin must work without them, and the tests stub them instead.
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.messages",
    "django.contrib.sessions",
    "django.contrib.staticfiles",
    "django_celery_beat",
    "esi",
    "aa_altcorp",
]
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
MIDDLEWARE = [
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ]
        },
    }
]

# Placeholders so django-esi's security system checks pass; nothing talks to ESI.
ESI_SSO_CLIENT_ID = "testauth"
ESI_SSO_CLIENT_SECRET = "testauth"
ESI_SSO_CALLBACK_URL = "https://localhost/sso/callback"
ESI_USER_CONTACT_EMAIL = "maintainer@example.com"
