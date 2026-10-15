"""App-Konfiguration für die PSA-Prüfung."""

from django.apps import AppConfig


class InspectionsConfig(AppConfig):
    """Prüfungsrunden, Prüfergebnisse und ihr Abschluss."""

    name = "inspections"
    verbose_name = "PSA-Prüfung"
