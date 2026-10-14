"""ASGI-Einstiegspunkt (derzeit ungenutzt, für spätere Erweiterungen)."""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "psagent.settings.prod")

application = get_asgi_application()
