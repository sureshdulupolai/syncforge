"""
Django settings for SyncForge.

All secrets and environment-specific values are read from environment variables.
Copy config/.env.example to config/.env and fill in your values.
For production, set variables directly in your hosting environment.
"""

import os
from pathlib import Path

# ─── Paths ────────────────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).resolve().parent.parent


# ─── Environment Helper ───────────────────────────────────────────────────────

def _env(key: str, default=None, required: bool = False):
    """Read an environment variable. Raises if required and missing."""
    val = os.environ.get(key, default)
    if required and not val:
        raise EnvironmentError(
            f"[SyncForge] Required environment variable '{key}' is not set. "
            f"See config/.env.example for setup instructions."
        )
    return val


# ─── Load .env file (development convenience) ────────────────────────────────
# In production, set env vars directly in your hosting environment.
_env_file = BASE_DIR / 'config' / '.env'
if _env_file.exists():
    with open(_env_file) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, _, value = line.partition('=')
                os.environ.setdefault(key.strip(), value.strip())


# ─── Core Security ────────────────────────────────────────────────────────────

SECRET_KEY = _env('DJANGO_SECRET_KEY', required=True)

DEBUG = _env('DEBUG', 'False') == 'True'

ALLOWED_HOSTS = [h.strip() for h in _env('ALLOWED_HOSTS', 'localhost,127.0.0.1').split(',') if h.strip()]


# ─── Application Definition ───────────────────────────────────────────────────

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',

    # SyncForge apps
    'core',
    'dashboard',
    'api',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    # Unified: handles X-API-Key + JWT cookie + Django session
    'api.middleware.UnifiedAuthMiddleware',
]

ROOT_URLCONF = 'config.urls'

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

WSGI_APPLICATION = 'config.wsgi.application'


# ─── Database ─────────────────────────────────────────────────────────────────
# This project uses SQLite. No additional configuration required.

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME':   BASE_DIR / 'db.sqlite3',
        'OPTIONS': {
            'timeout': 20,
        }
    }
}


# ─── Cache ────────────────────────────────────────────────────────────────────
# Using Django's built-in in-memory cache (single-process development).

CACHES = {
    'default': {
        'BACKEND':  'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'syncforge-cache',
    }
}


# ─── Password Validation ──────────────────────────────────────────────────────

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
     'OPTIONS': {'min_length': 8}},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]


# ─── Internationalisation ─────────────────────────────────────────────────────

LANGUAGE_CODE = 'en-us'
TIME_ZONE     = 'UTC'
USE_I18N      = True
USE_TZ        = True


# ─── Static Files ─────────────────────────────────────────────────────────────

STATIC_URL  = 'static/'
STATICFILES_DIRS = [BASE_DIR / 'static']


# ─── Auth Redirects ───────────────────────────────────────────────────────────

LOGIN_REDIRECT_URL  = 'dashboard'
LOGIN_URL           = 'login'
LOGOUT_REDIRECT_URL = 'home'


# ─── Default Primary Key ──────────────────────────────────────────────────────

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'


# ─── Super Admin Registration Gate ───────────────────────────────────────────
# Set SUPER_REGISTER_PASSWORD in your environment.
# This password is required to access the hidden superuser registration page.

SUPER_REGISTER_PASSWORD = _env('SUPER_REGISTER_PASSWORD', required=True)


# ─── JWT Authentication ───────────────────────────────────────────────────────
# JWT_SECRET_KEY is intentionally separate from Django's SECRET_KEY.
# Rotating one does not affect the other.

JWT_SECRET_KEY = _env('JWT_SECRET_KEY', required=True)

_jwt_algo = _env('JWT_ALGORITHM', 'HS256')
assert _jwt_algo != 'none', "[SyncForge] JWT algorithm 'none' is not permitted."
JWT_ALGORITHM = _jwt_algo

JWT_ACCESS_TOKEN_EXPIRE_MINUTES = int(_env('JWT_ACCESS_TOKEN_EXPIRE_MINUTES', '15'))
JWT_REFRESH_TOKEN_EXPIRE_DAYS   = int(_env('JWT_REFRESH_TOKEN_EXPIRE_DAYS', '7'))


# ─── Session ──────────────────────────────────────────────────────────────────

SESSION_COOKIE_AGE       = 900    # 15 minutes
SESSION_SAVE_EVERY_REQUEST = True
SESSION_COOKIE_HTTPONLY  = True
SESSION_COOKIE_SAMESITE  = 'Lax'
SESSION_COOKIE_SECURE    = not DEBUG  # True in production


# ─── Security Headers (production) ────────────────────────────────────────────

if not DEBUG:
    SECURE_HSTS_SECONDS             = 31536000   # 1 year
    SECURE_HSTS_INCLUDE_SUBDOMAINS  = True
    SECURE_HSTS_PRELOAD             = True
    SECURE_SSL_REDIRECT             = True
    CSRF_COOKIE_SECURE              = True
    SESSION_COOKIE_SECURE           = True
