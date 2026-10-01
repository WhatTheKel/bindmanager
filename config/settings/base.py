from pathlib import Path
from decouple import config, Csv

BASE_DIR = Path(__file__).resolve().parent.parent.parent

SECRET_KEY = config('DJANGO_SECRET_KEY')
DEBUG = config('DJANGO_DEBUG', default=False, cast=bool)
ALLOWED_HOSTS = config('DJANGO_ALLOWED_HOSTS', default='localhost', cast=Csv())
CSRF_TRUSTED_ORIGINS = config('CSRF_TRUSTED_ORIGINS', default='http://localhost', cast=Csv())
USE_X_FORWARDED_HOST = True
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

# How many reverse proxies we run in front of the app (1 = the bundled
# nginx). The client address is taken that many entries from the *right* of
# X-Forwarded-For, never the first entry (which the client can fake). Raise
# it only if you add another proxy (e.g. a load balancer) in front of nginx.
TRUSTED_PROXY_COUNT = config('TRUSTED_PROXY_COUNT', default=1, cast=int)

DJANGO_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
]

THIRD_PARTY_APPS = [
    'rest_framework',
    'rest_framework_simplejwt',
    'django_celery_beat',
    'social_django',
]

LOCAL_APPS = [
    'apps.dns_manager',
    'apps.api',
    'apps.accounts',
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'frontend' / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'apps.accounts.context_processors.sso_providers',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'

_DB_ENGINE = config('DB_ENGINE', default='mysql').lower()

if _DB_ENGINE == 'postgresql':
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': config('DB_NAME', default='bindmanager'),
            'USER': config('DB_USER', default='bindmanager'),
            'PASSWORD': config('DB_PASSWORD', default='bindmanager'),
            'HOST': config('DB_HOST', default='localhost'),
            'PORT': config('DB_PORT', default='5432'),
            'CONN_MAX_AGE': 60,
        }
    }
else:  # default: mysql
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.mysql',
            'NAME': config('DB_NAME', default='bindmanager'),
            'USER': config('DB_USER', default='bindmanager'),
            'PASSWORD': config('DB_PASSWORD', default='bindmanager'),
            'HOST': config('DB_HOST', default='localhost'),
            'PORT': config('DB_PORT', default='3306'),
            'CONN_MAX_AGE': 60,
            'OPTIONS': {
                'charset': 'utf8mb4',
                'init_command': "SET sql_mode='STRICT_TRANS_TABLES'",
            },
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / 'frontend' / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

SESSION_COOKIE_AGE = config('SESSION_COOKIE_AGE', default=28800, cast=int)  # 8 hours
SESSION_SAVE_EVERY_REQUEST = False

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '{asctime} {levelname} {name} {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
    },
    'root': {'handlers': ['console'], 'level': 'WARNING'},
    'loggers': {
        'django': {
            'handlers': ['console'], 'level': 'WARNING', 'propagate': False,
        },
        'django.security': {
            'handlers': ['console'], 'level': 'INFO', 'propagate': False,
        },
        'apps': {
            'handlers': ['console'], 'level': 'INFO', 'propagate': False,
        },
    },
}

# Celery
# Shared by all gunicorn workers: login lockout counters and API rate limits
# must be counted in one place, not per worker process.
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.redis.RedisCache',
        'LOCATION': config('REDIS_URL', default='redis://redis:6379/0'),
        'KEY_PREFIX': 'bindmanager',
    }
}

CELERY_BROKER_URL = config('REDIS_URL', default='redis://redis:6379/0')
CELERY_RESULT_BACKEND = config('REDIS_URL', default='redis://redis:6379/0')
CELERY_BEAT_SCHEDULER = 'django_celery_beat.schedulers:DatabaseScheduler'
CELERY_TASK_ROUTES = {'apps.dns_manager.tasks.*': {'queue': 'zone_sync'}}
CELERY_TASK_IGNORE_RESULT = True

# DRF
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework_simplejwt.authentication.JWTAuthentication',
        'rest_framework.authentication.SessionAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 50,
    # Same rule as apps/accounts/client_ip.py, so API rate limits (including
    # /api/token/) can't be dodged with a fake X-Forwarded-For.
    'NUM_PROXIES': TRUSTED_PROXY_COUNT,
    'DEFAULT_THROTTLE_CLASSES': [
        'rest_framework.throttling.AnonRateThrottle',
        'rest_framework.throttling.UserRateThrottle',
    ],
    'DEFAULT_THROTTLE_RATES': {
        'anon': '20/minute',
        'user': '300/minute',
        # Pull-agent endpoints (apps/api/v1/agent_views.py) poll far more often
        # than a human/JWT client and authenticate via NameServer API key, not
        # a Django user, so they get their own scope instead of the anon rate.
        'agent': '300/minute',
    },
}

# JWT
from datetime import timedelta  # noqa: E402
SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(minutes=15),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=1),
    'ROTATE_REFRESH_TOKENS': True,
    'BLACKLIST_AFTER_ROTATION': False,
    'ALGORITHM': 'HS256',
    'AUTH_HEADER_TYPES': ('Bearer',),
}

# DNS zone sync settings
BIND_ZONES_DIR = config('BIND_ZONES_DIR', default='/etc/bind/zones')
RNDC_BIN = config('RNDC_BIN', default='/usr/sbin/rndc')

# Auth
LOGIN_REDIRECT_URL = '/dashboard/'
LOGOUT_REDIRECT_URL = '/accounts/login/'
SOCIAL_AUTH_URL_NAMESPACE = 'social'
SOCIAL_AUTH_LOGIN_ERROR_URL = '/accounts/login/?sso_error=1'

# Branding — volume-mount files into BASE_DIR/branding/, or override with explicit URLs
BRANDING_DIR         = BASE_DIR / 'branding'
BRANDING_LOGO_URL    = config('BRANDING_LOGO_URL',    default='')
BRANDING_FAVICON_URL = config('BRANDING_FAVICON_URL', default='')
BRANDING_APP_NAME    = config('BRANDING_APP_NAME',    default='BindManager')

# Shown in the page footer. Bump the VERSION file (and tag the commit) when
# releasing; see UPDATING.md.
try:
    APP_VERSION = (BASE_DIR / 'VERSION').read_text().strip()
except OSError:
    APP_VERSION = ''

# The pull agent version this release ships (AGENT_VERSION in
# agents/bindmanager_agent.py). Nameservers reporting an older one are
# flagged as outdated in Manage > Nameservers.
import re as _re
try:
    _m = _re.search(r"^AGENT_VERSION = '([^']+)'",
                    (BASE_DIR / 'agents' / 'bindmanager_agent.py').read_text(), _re.M)
    LATEST_AGENT_VERSION = _m.group(1) if _m else ''
except OSError:
    LATEST_AGENT_VERSION = ''
# An agent that hasn't checked in for this long is flagged (timer runs every 2 min)
AGENT_STALE_AFTER_MINUTES = 10

# SSO feature flags — explicit opt-in; backends are not registered unless enabled
OKTA_SSO_ENABLED = config('OKTA_ENABLED', default=False, cast=bool)
AUTHENTIK_SSO_ENABLED = config('AUTHENTIK_ENABLED', default=False, cast=bool)

AUTHENTICATION_BACKENDS = ['django.contrib.auth.backends.ModelBackend']
if AUTHENTIK_SSO_ENABLED:
    AUTHENTICATION_BACKENDS.append('apps.accounts.backends.AuthentikOpenIdConnect')
if OKTA_SSO_ENABLED:
    AUTHENTICATION_BACKENDS.append('social_core.backends.okta_openidconnect.OktaOpenIdConnect')

# Okta OIDC — set OKTA_BASE_URL to your org URL, e.g. https://company.okta.com
SOCIAL_AUTH_OKTA_OPENIDCONNECT_KEY = config('OKTA_CLIENT_ID', default='')
SOCIAL_AUTH_OKTA_OPENIDCONNECT_SECRET = config('OKTA_CLIENT_SECRET', default='')
SOCIAL_AUTH_OKTA_OPENIDCONNECT_API_URL = config('OKTA_BASE_URL', default='')

# Authentik OIDC — set AUTHENTIK_OIDC_ENDPOINT to the application issuer URL,
# e.g. https://auth.example.com/application/o/bindmanager/
SOCIAL_AUTH_AUTHENTIK_KEY = config('AUTHENTIK_CLIENT_ID', default='')
SOCIAL_AUTH_AUTHENTIK_SECRET = config('AUTHENTIK_CLIENT_SECRET', default='')
SOCIAL_AUTH_AUTHENTIK_OIDC_ENDPOINT = config('AUTHENTIK_OIDC_ENDPOINT', default='')

# ignore_session_user: an SSO sign-in never links to whoever is already
# logged in. associate_by_email is deliberately absent — matching on an email
# the IdP reports would let an SSO account take over a local one (including
# a superuser) with the same address.
SOCIAL_AUTH_PIPELINE = (
    'apps.accounts.pipeline.ignore_session_user',
    'social_core.pipeline.social_auth.social_details',
    'social_core.pipeline.social_auth.social_uid',
    'social_core.pipeline.social_auth.social_user',
    'social_core.pipeline.user.get_username',
    'social_core.pipeline.user.create_user',
    'social_core.pipeline.social_auth.associate_user',
    'social_core.pipeline.social_auth.load_extra_data',
    'social_core.pipeline.user.user_details',
    'apps.accounts.pipeline.set_staff_flag',
)
