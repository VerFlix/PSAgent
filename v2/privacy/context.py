"""Datenschutzhinweis und Löschfrist für alle Vorlagen."""

from django.conf import settings
from django.http import HttpRequest

from privacy.notice import NO_NAMES, retention_notice


def retention(request: HttpRequest) -> dict[str, object]:
    return {
        "retention_years": settings.PSAGENT_RETENTION_YEARS,
        "retention_notice": retention_notice(),
        "no_names": NO_NAMES,
    }
