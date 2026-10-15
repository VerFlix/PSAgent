"""App-Konfiguration für gemeinsame Grundlagen."""

from django.apps import AppConfig


class CoreConfig(AppConfig):
    """Gemeinsame Grundlagen, z. B. der Schutz für Tabellen, die nur angehängt werden."""

    name = "core"
    verbose_name = "Grundlagen"
