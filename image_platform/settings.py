import os
import sys
from pathlib import Path
from urllib.parse import urlparse


BASE_DIR = Path(__file__).resolve().parent.parent


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return int(value)


SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "dev-only-change-me")
DEBUG = env_bool("DJANGO_DEBUG", False)
ALLOWED_HOSTS = [
    host.strip()
    for host in os.getenv("DJANGO_ALLOWED_HOSTS", "127.0.0.1,localhost").split(",")
    if host.strip()
]
CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in os.getenv("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",")
    if origin.strip()
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "platform_app",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "image_platform.urls"

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
    }
]

WSGI_APPLICATION = "image_platform.wsgi.application"


def database_config():
    running_pytest = any("pytest" in arg for arg in sys.argv)
    if env_bool("USE_SQLITE_FOR_TESTS", running_pytest):
        return {
            "default": {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": BASE_DIR / "db.sqlite3",
            }
        }

    parsed = urlparse(
        os.getenv(
            "DATABASE_URL",
            "postgres://image_platform:image_platform@localhost:5432/image_platform",
        )
    )
    return {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": parsed.path.lstrip("/") or "image_platform",
            "USER": parsed.username or "image_platform",
            "PASSWORD": parsed.password or "image_platform",
            "HOST": parsed.hostname or "localhost",
            "PORT": str(parsed.port or 5432),
        }
    }


DATABASES = database_config()

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 12}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "zh-hans"
TIME_ZONE = "Asia/Shanghai"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "media/"
LOCAL_MEDIA_ROOT = Path(os.getenv("LOCAL_MEDIA_ROOT", BASE_DIR / "media"))
MEDIA_ROOT = LOCAL_MEDIA_ROOT
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
AUTH_USER_MODEL = "platform_app.User"

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "batch_list"
LOGOUT_REDIRECT_URL = "login"
SESSION_COOKIE_AGE = 12 * 60 * 60
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

APIMART_API_KEY = os.getenv("APIMART_API_KEY", "")
APIMART_BASE_URL = os.getenv("APIMART_BASE_URL", "https://api.apimart.ai").rstrip("/")
APIMART_PROMPT_MODEL = os.getenv("APIMART_PROMPT_MODEL", "gpt-5.2-pro")
APIMART_FAKE_MODE = env_bool("APIMART_FAKE_MODE", True)
STORAGE_BACKEND = os.getenv("STORAGE_BACKEND", "local")
MAX_ACTIVE_GENERATIONS = env_int("MAX_ACTIVE_GENERATIONS", 2)
ORG_DAILY_GENERATION_LIMIT = env_int("ORG_DAILY_GENERATION_LIMIT", 2000)
USER_DAILY_GENERATION_LIMIT = env_int("USER_DAILY_GENERATION_LIMIT", 100)
