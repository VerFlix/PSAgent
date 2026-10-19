"""Verwaltungsbefehl: Kettenkopf täglich festhalten, stempeln und bezeugen (#16, #17).

Für den täglichen Timer auf dem Pi. Scheitert ein Schritt (Zeitstempel, Datei, Mail), laufen die
übrigen trotzdem; am Ende meldet der Befehl einen Fehler, damit das Monitoring es sieht.
"""

from typing import Any

from django.core.management.base import BaseCommand, CommandError

from core import witness


class Command(BaseCommand):
    help = "Hält den Kettenkopf mit Zeitstempel fest und schickt ihn an das Zeugen-Postfach."

    def handle(self, *args: Any, **options: Any) -> None:
        try:
            head, problem = witness.capture()
        except witness.WitnessError as error:
            raise CommandError(str(error)) from error
        failures = [problem] if problem else []
        failures += witness.deliver(head)
        stamp = "mit Zeitstempel" if head.token is not None else "ohne Zeitstempel"
        self.stdout.write(f"Kettenkopf {head.seq}:{head.head_hash} festgehalten, {stamp}.")
        if failures:
            for failure in failures:
                self.stderr.write(f"  {failure}")
            raise CommandError(f"Kettenkopf: {len(failures)} Fehler.")
        self.stdout.write(
            "Zeugen-Mail gesendet; Datei geschrieben, falls ein Ordner eingetragen ist."
        )
