"""App-Konfiguration für den Datenschutz."""

from django.apps import AppConfig


class PrivacyConfig(AppConfig):
    """Löschen von Personendaten nach der Löschfrist (#63)."""

    name = "privacy"
    verbose_name = "Datenschutz"
