"""Lokale Entwicklung. Niemals auf dem Pi verwenden."""

from .base import *

DEBUG = True
SECRET_KEY = "django-insecure-nur-fuer-die-lokale-entwicklung"
ALLOWED_HOSTS = ["localhost", "127.0.0.1"]

DATABASES = {"default": postgres(env("POSTGRES_PASSWORD", "psagent-dev"))}
