"""Nur für den Image-Bau: `collectstatic` ohne Geheimnisse und ohne Datenbank (#22).

Gleiche Ablage der statischen Dateien wie im Betrieb (`prod`); sonst fehlt dort das Manifest.
"""

from .base import *

SECRET_KEY = "nur-fuer-collectstatic-im-image-bau"
STORAGES = WHITENOISE_STORAGES
