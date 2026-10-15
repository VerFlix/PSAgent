"""Gemeinsame Einstellungen für alle Umgebungen.

Werte, die sich je Umgebung unterscheiden oder geheim sind, kommen aus
Umgebungsvariablen. Geheimnisse stehen nie im Code (siehe CLAUDE.md).
"""

import os
from pathlib import Path
from typing import Any

from django.core.exceptions import ImproperlyConfigured
from django.utils.csp import CSP

BASE_DIR = Path(__file__).resolve().parent.parent.parent


def env(name: str, default: str | None = None) -> str:
    """Liest eine Umgebungsvariable; ohne Standardwert ist sie Pflicht."""
    value = os.environ.get(name, default)
    if value is None:
        raise ImproperlyConfigured(f"Umgebungsvariable {name} fehlt.")
    return value


def env_list(name: str, default: str = "") -> list[str]:
    """Liest eine kommagetrennte Liste aus einer Umgebungsvariable."""
    return [item.strip() for item in env(name, default).split(",") if item.strip()]


def postgres(password: str) -> dict[str, Any]:
    """Baut die Datenbankverbindung. Jede Umgebung bekommt ein eigenes Dict."""
    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env("POSTGRES_DB", "psagent"),
        "USER": env("POSTGRES_USER", "psagent"),
        "PASSWORD": password,
        "HOST": env("POSTGRES_HOST", "localhost"),
        "PORT": env("POSTGRES_PORT", "5432"),
        "CONN_MAX_AGE": 60,
        "CONN_HEALTH_CHECKS": True,
    }


DEBUG = False
ALLOWED_HOSTS: list[str] = []

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.postgres",
    "core",
    "accounts",
    "inventory",
    "inspections",
    "lending",
    "legacy",
]

# Eigenes Benutzermodell mit Rolle; muss vor der ersten Migration feststehen.
AUTH_USER_MODEL = "accounts.User"

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.csp.ContentSecurityPolicyMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "psagent.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.template.context_processors.csp",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "psagent.wsgi.application"

DATABASES = {"default": postgres(env("POSTGRES_PASSWORD", ""))}

# Argon2 zuerst: aktueller Standard für Passwort-Hashes.
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher",
    "django.contrib.auth.hashers.ScryptPasswordHasher",
]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 12},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "de-de"
TIME_ZONE = "Europe/Berlin"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# Hochgeladene Dateien (GAL); liegen nie im Repo (.gitignore: v2/media/).
MEDIA_URL = "media/"
MEDIA_ROOT = Path(env("PSAGENT_MEDIA_ROOT", str(BASE_DIR / "media")))

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Sitzungen laufen nach 8 Stunden ab; am Vereins-Tablet kommt später ein
# kürzeres Abmelden nach jedem Vorgang dazu (Konzept, Abschnitt 5).
SESSION_COOKIE_AGE = 60 * 60 * 8

# Grundschutz in allen Umgebungen
X_FRAME_OPTIONS = "DENY"
SECURE_REFERRER_POLICY = "same-origin"
SECURE_CONTENT_TYPE_NOSNIFF = True

# Content-Security-Policy: nur eigene Quellen, Skripte und Styles mit Nonce.
SECURE_CSP = {
    "default-src": [CSP.SELF],
    "script-src": [CSP.SELF, CSP.NONCE],
    "style-src": [CSP.SELF, CSP.NONCE],
    "img-src": [CSP.SELF, "data:"],
    "object-src": [CSP.NONE],
    "base-uri": [CSP.SELF],
    "form-action": [CSP.SELF],
    "frame-ancestors": [CSP.NONE],
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}
