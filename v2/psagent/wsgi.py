"""WSGI-Einstiegspunkt für gunicorn im Betrieb."""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "psagent.settings.prod")

application = get_wsgi_application()
