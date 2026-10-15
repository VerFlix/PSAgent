"""App-Konfiguration für den Verleih."""

from django.apps import AppConfig


class LendingConfig(AppConfig):
    """Entleiher, Reservierungen, Ausleihen und Rückgaben."""

    name = "lending"
    verbose_name = "Verleih"
