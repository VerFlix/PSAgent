"""App-Konfiguration für Benutzer, Rollen und Sachkunde."""

from django.apps import AppConfig


class AccountsConfig(AppConfig):
    """Benutzerkonten mit Rolle und der Qualifikation Sachkunde."""

    name = "accounts"
    verbose_name = "Benutzer und Sachkunde"
