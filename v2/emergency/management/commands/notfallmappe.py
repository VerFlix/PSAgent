"""Verwaltungsbefehl: Notfallmappe erzeugen und speichern (für den wöchentlichen Timer)."""

from typing import Any

from django.core.management.base import BaseCommand

from emergency import binder as service


class Command(BaseCommand):
    help = "Erzeugt eine Notfallmappe mit neuen Formularnummern und speichert sie."

    def handle(self, *args: Any, **options: Any) -> None:
        binder = service.create_binder(actor=None)
        self.stdout.write(f"{binder} gespeichert: {service.path_of(binder)}")
        for title, numbers in service.ranges(binder):
            self.stdout.write(f"  {title}: {numbers}")
