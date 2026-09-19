"""A real Alliance Auth project, used to test the plugin's integration.

This is the counterpart to ``tests/settings.py``, and the two exist for opposite
reasons:

* ``tests/settings.py`` leaves Alliance Auth, aa-contacts and the Discord
  libraries **out** of INSTALLED_APPS, so the suite proves the plugin degrades
  gracefully without them.  ``tests/test_import_isolation.py`` guards that.
* this module installs them, so the suite proves the integration actually works:
  ``AuthSnapshot.from_auth``, the ``auth_hooks`` registrations, the
  ``services.search_*`` helpers, and the admin form choices.

Several settings here are load-bearing rather than cosmetic; each is commented.
"""

import fakeredis

SECRET_KEY = "aa-altcorp-test-key"
DEBUG = False
ALLOWED_HOSTS = ["*"]
USE_TZ = True
USE_I18N = True
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
DEFAULT_AUTO_FIELD = "django.db.models.AutoField"

# Alliance Auth mounts the plugin itself, from the url_hook in
# aa_altcorp.auth_hooks, so routing through its URLconf is what exercises that
# registration. Note it wraps hooked views in main_character_required, so a test
# user needs a main character or it is redirected to the dashboard.
ROOT_URLCONF = "allianceauth.urls"
WSGI_APPLICATION = "testauth.wsgi.application"

DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}

# allianceauth must come first: it supplies the base templates, and its static
# files back the favicons the Django admin pulls in.
INSTALLED_APPS = [
    "allianceauth",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "django_celery_beat",
    "django_bootstrap5",
    "esi",
    # django-sri provides the {% sri %} tag the Auth themes load.
    "sri",
    "allianceauth.framework",
    "allianceauth.authentication",
    "allianceauth.eveonline",
    "allianceauth.notifications",
    # services and groupmanagement are not optional. Their models get imported
    # transitively, and without their own app entry Django binds them to the
    # root allianceauth app, which has no migrations -- at which point the proxy
    # groupmanagement.Group fails migrate with InvalidBasesError.
    "allianceauth.services",
    "allianceauth.groupmanagement",
    "allianceauth.menu",
    "allianceauth.theme",
    # Whatever DEFAULT_THEME names below has to be installed.
    "allianceauth.theme.flatly",
    "allianceauth.custom_css",
    "allianceauth.thirdparty.navhelper",
    "aa_contacts",
    "aa_altcorp",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "allianceauth.authentication.middleware.UserSettingsMiddleware",
    "allianceauth.middleware.DeviceDetectionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

AUTHENTICATION_BACKENDS = [
    "allianceauth.authentication.backends.StateBackend",
    "django.contrib.auth.backends.ModelBackend",
]

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "django.template.context_processors.i18n",
                "django.template.context_processors.static",
                "django.template.context_processors.tz",
                # Supplies SITE_NAME and friends, which the base template reads.
                "allianceauth.context_processors.auth_settings",
            ]
        },
    }
]

# A local-memory or dummy cache cannot be used here. Importing
# allianceauth.authentication builds its task-statistics counters eagerly, which
# calls django_redis.get_redis_connection("default"); django-redis raises
# NotImplementedError for a non-redis backend and Auth only catches
# AttributeError. fakeredis gives a real, working client with no server.
CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": "redis://127.0.0.1:6379/1",
        "OPTIONS": {
            "CLIENT_CLASS": "django_redis.client.DefaultClient",
            "CONNECTION_POOL_KWARGS": {"connection_class": fakeredis.FakeConnection},
        },
    }
}
# Auth defaults to cached_db, which would route every request through the cache.
SESSION_ENGINE = "django.contrib.sessions.backends.db"

STATIC_URL = "/static/"
# django-sri reads USE_SRI at module import, so @override_settings cannot reach
# it. Left on with DEBUG=False it calls staticfiles_storage.path() and raises
# ImproperlyConfigured for the missing STATIC_ROOT.
USE_SRI = False
SRI_ALGORITHM = "sha512"

SITE_NAME = "Test Auth"
SITE_URL = "https://example.com"
CSRF_TRUSTED_ORIGINS = ["https://example.com"]
DEFAULT_THEME = "allianceauth.theme.flatly.auth_hooks.FlatlyThemeHook"
LOGIN_URL = "auth_login_user"
LOGIN_REDIRECT_URL = "authentication:dashboard"
LOGOUT_REDIRECT_URL = "authentication:dashboard"
# A system check fails on an empty scope list.
LOGIN_TOKEN_SCOPES = ["publicData"]

# Placeholders so django-esi's security system checks pass; nothing talks to ESI.
ESI_SSO_CLIENT_ID = "testauth"
ESI_SSO_CLIENT_SECRET = "testauth"
ESI_SSO_CALLBACK_URL = "https://example.com/sso/callback"
ESI_USER_CONTACT_EMAIL = "maintainer@example.com"
ESI_CACHE_RESPONSE = False

# Drops only Alliance Auth's Redis/MySQL version probes, which raise against
# fakeredis and SQLite. See testauth/runner.py.
TEST_RUNNER = "testauth.runner.SkipEnvironmentChecksRunner"

CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
