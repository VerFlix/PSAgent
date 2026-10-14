"""Einstellungen für pytest und die CI."""

from .base import *

SECRET_KEY = "nur-fuer-tests"

# Schneller Hash nur in Tests; im Betrieb gilt Argon2 aus base.py.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

DATABASES = {"default": postgres(env("POSTGRES_PASSWORD", "psagent-dev"))}
