"""App-Konfiguration für die Anbindung an das Altsystem."""

from django.apps import AppConfig


class LegacyConfig(AppConfig):
    """Altsystem: Schema-Varianten, Testdaten-Generator und (ab #10) Import."""

    name = "legacy"
    verbose_name = "Altsystem"
