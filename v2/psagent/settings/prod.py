"""Betrieb auf dem Pi hinter Caddy (HTTPS) im Tailnet.

Alle Geheimnisse kommen aus Umgebungsvariablen. Fehlt eine Pflichtvariable,
startet die Anwendung nicht.
"""

from .base import *

DEBUG = False
SECRET_KEY = env("DJANGO_SECRET_KEY")
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS")
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")

DATABASES = {"default": postgres(env("POSTGRES_PASSWORD"))}

# Caddy beendet TLS und meldet das Protokoll per Header.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = True
SECURE_REDIRECT_EXEMPT = [r"^healthz$"]

SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

# HSTS nur für den eigenen Namen; die ts.net-Domain gehört Tailscale.
SECURE_HSTS_SECONDS = 60 * 60 * 24 * 365
SECURE_HSTS_INCLUDE_SUBDOMAINS = False
SECURE_HSTS_PRELOAD = False
