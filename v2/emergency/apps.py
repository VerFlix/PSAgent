"""App-Konfiguration für den Notbetrieb."""

from django.apps import AppConfig


class EmergencyConfig(AppConfig):
    """Notfallmappe, Papierformulare und (später) Nacherfassung (Parität 22)."""

    name = "emergency"
    verbose_name = "Notbetrieb"
