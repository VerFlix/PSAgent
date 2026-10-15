"""App-Konfiguration für den Bestand."""

from django.apps import AppConfig


class InventoryConfig(AppConfig):
    """Teile, Bauteile, GAL-Dokumente und Vorgänge am Teil."""

    name = "inventory"
    verbose_name = "Bestand"
