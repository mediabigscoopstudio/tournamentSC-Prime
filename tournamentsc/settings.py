"""
Django settings for TournamentSC.

Built to the TournamentSC Technical Architecture Document, following the
BigScoop Founders studio project standard (custom-branded dashboard with its
own login flow; Django's built-in admin disabled for the public).
"""
from pathlib import Path
import os

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / '.env')


def env_bool(key, default=False):
    return os.environ.get(key, str(default)).strip().lower() in ('1', 'true', 'yes', 'on')


def env_list(key, default=''):
    raw = os.environ.get(key, default)
    return [item.strip() for item in raw.split(',') if item.strip()]


# --- Core ---------------------------------------------------------------
SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY', 'dev-insecure-change-me-in-production')
DEBUG = env_bool('DJANGO_DEBUG', True)
# In dev, accept any host (incl. the test client's 'testserver'). In production
# (DEBUG=False) the host list is locked down to DJANGO_ALLOWED_HOSTS.
ALLOWED_HOSTS = ['*'] if DEBUG else env_list('DJANGO_ALLOWED_HOSTS')

# Public users never reach Django's built-in admin. It is OFF by default and,
# when enabled for developers, lives at an obscure, env-controlled path.
ENABLE_DJANGO_ADMIN = env_bool('ENABLE_DJANGO_ADMIN', False)
DJANGO_ADMIN_URL = os.environ.get('DJANGO_ADMIN_URL', 'django-admin-dev/')

# --- Applications -------------------------------------------------------
INSTALLED_APPS = [
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',

    # Project apps
    'accounts',       # custom User, profiles, auth, notifications, follows
    'tournaments',    # sports, tournaments, teams, fixtures, scoring, engines
    'dash',           # custom-branded platform admin dashboard
    'main',           # public audience site + organizer/player dashboards
    'support',        # customer support tickets (player + organizer contact form)
]

# django.contrib.admin only loaded when a developer explicitly enables it.
if ENABLE_DJANGO_ADMIN:
    INSTALLED_APPS.insert(0, 'django.contrib.admin')

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware' if os.environ.get('USE_WHITENOISE') else 'django.middleware.common.CommonMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    # Must sit AFTER MessageMiddleware: it calls messages.error() on the way in,
    # which needs request._messages to already exist.
    'accounts.middleware.SuspendedUserMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]
# De-duplicate the CommonMiddleware placeholder above when WhiteNoise isn't used.
MIDDLEWARE = list(dict.fromkeys(MIDDLEWARE))

ROOT_URLCONF = 'tournamentsc.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        # BigScoop standard: directory is 'template' (singular).
        'DIRS': [BASE_DIR / 'template'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'accounts.context_processors.notifications',
                'tournaments.context_processors.ticker',
                'dash.context_processors.site',
            ],
        },
    },
]

WSGI_APPLICATION = 'tournamentsc.wsgi.application'
ASGI_APPLICATION = 'tournamentsc.asgi.application'

# --- Database -----------------------------------------------------------
# Dev default: SQLite (zero-config, runnable anywhere). Production: set
# DATABASE_URL to a PostgreSQL DSN (per the TRD) and it is parsed below.
DATABASE_URL = os.environ.get('DATABASE_URL', '')
if DATABASE_URL.startswith('postgres'):
    from urllib.parse import urlparse
    u = urlparse(DATABASE_URL)
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': u.path.lstrip('/'),
            'USER': u.username,
            'PASSWORD': u.password,
            'HOST': u.hostname,
            'PORT': u.port or '5432',
        }
    }
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
        }
    }

# --- Auth ---------------------------------------------------------------
AUTH_USER_MODEL = 'accounts.User'
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]
# The public "login" URL is a *chooser* page, not a login form — each role has its
# own door (/player/login, /organizer/login, /dashboard/login) and the view-layer
# guards in accounts.decorators send a visitor to the right one. Nothing in the
# project relies on Django's @login_required, so this only serves as a safe default.
LOGIN_URL = '/login'
LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = '/'

# --- I18n / TZ ----------------------------------------------------------
LANGUAGE_CODE = 'en-us'
TIME_ZONE = os.environ.get('TIME_ZONE', 'Asia/Kolkata')
USE_I18N = True
USE_TZ = True

# --- Static & Media -----------------------------------------------------
STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# --- Email (console in dev, Hostinger SMTP for support@tournamentsc.com) ---
EMAIL_BACKEND = os.environ.get(
    'EMAIL_BACKEND', 'django.core.mail.backends.console.EmailBackend'
)
EMAIL_HOST = os.environ.get('EMAIL_HOST', 'smtp.hostinger.com')
EMAIL_PORT = int(os.environ.get('EMAIL_PORT', '465'))
EMAIL_USE_SSL = env_bool('EMAIL_USE_SSL', True)
EMAIL_USE_TLS = env_bool('EMAIL_USE_TLS', False)
EMAIL_HOST_USER = os.environ.get('EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = os.environ.get('EMAIL_HOST_PASSWORD', '')
DEFAULT_FROM_EMAIL = os.environ.get('DEFAULT_FROM_EMAIL', 'support@tournamentsc.com')

# The live domain — used to build absolute links/images (e.g. the logo) in emails.
SITE_URL = os.environ.get('SITE_URL', 'https://tournamentsc.com')

# --- Celery (async email — a slow/unreachable SMTP server never blocks a
# request, and sending many emails at once doesn't pile up on one thread) ---
CELERY_BROKER_URL = os.environ.get('CELERY_BROKER_URL', 'redis://localhost:6379/0')
CELERY_RESULT_BACKEND = os.environ.get('CELERY_RESULT_BACKEND', CELERY_BROKER_URL)
CELERY_TASK_SERIALIZER = 'json'
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TIMEZONE = os.environ.get('TIME_ZONE', 'Asia/Kolkata')
# Dev default: run tasks inline, no worker/Redis required locally. Production
# must set this to False (and run `celery -A tournamentsc worker`) so email
# sending actually happens off the request thread.
CELERY_TASK_ALWAYS_EAGER = env_bool('CELERY_TASK_ALWAYS_EAGER', DEBUG)
CELERY_TASK_EAGER_PROPAGATES = False

# --- Messages -> CSS class map (matches style-guide semantic colors) ----
from django.contrib.messages import constants as message_constants  # noqa: E402
MESSAGE_TAGS = {
    message_constants.DEBUG: 'info',
    message_constants.INFO: 'info',
    message_constants.SUCCESS: 'success',
    message_constants.WARNING: 'warning',
    message_constants.ERROR: 'error',
}

# --- Production security (only bite when DEBUG is off) ------------------
if not DEBUG:
    SECURE_SSL_REDIRECT = env_bool('SECURE_SSL_REDIRECT', True)
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = int(os.environ.get('SECURE_HSTS_SECONDS', '3600'))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    CSRF_TRUSTED_ORIGINS = env_list('CSRF_TRUSTED_ORIGINS')
    # Behind Nginx (or any reverse proxy) terminating TLS, Django only sees a
    # plain HTTP connection from the proxy. Without this, request.is_secure()
    # is always False and SECURE_SSL_REDIRECT above causes an infinite
    # redirect loop. Requires the proxy to set `X-Forwarded-Proto` — e.g.
    # `proxy_set_header X-Forwarded-Proto $scheme;` in the Nginx server block.
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
