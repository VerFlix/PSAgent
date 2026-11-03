"""App-Konfiguration für die Theke."""

from django.apps import AppConfig


class CounterConfig(AppConfig):
    """Touch-Oberfläche für die Ausgabe am Tablet (#127); nutzt die Services des Verleihs."""

    name = "counter"
    verbose_name = "Theke"
