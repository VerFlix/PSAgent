"""Verwaltungsbefehl: Erinnerungen bei Überfälligkeit senden (für den täglichen Timer, #72).

Nennt Vorgänge nur mit Nummer, damit im Protokoll des Timers keine Namen oder Adressen stehen.
"""

from datetime import date
from typing import Any

from django.core.management.base import BaseCommand, CommandParser

from lending import reminders


class Command(BaseCommand):
    help = "Sendet fällige E-Mail-Erinnerungen zu überfälligen Ausleihen."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--probelauf", action="store_true", help="Nur anzeigen, was gesendet würde."
        )
        parser.add_argument(
            "--stichtag", type=date.fromisoformat, help="Datum statt heute (JJJJ-MM-TT)."
        )

    def handle(self, *args: Any, **options: Any) -> None:
        if not reminders.enabled():
            self.stdout.write("Erinnerungen sind für alle ausgeschaltet.")
            return
        dry_run = options["probelauf"]
        result = reminders.run(options["stichtag"], dry_run=dry_run)
        for entry in result:
            self.stdout.write(f"  Ausleihvorgang #{entry.booking.pk}: {entry.number}. Erinnerung")
        if dry_run:
            self.stdout.write(f"Probelauf: {len(result)} Erinnerung(en) würden gesendet.")
        else:
            self.stdout.write(f"{len(result)} Erinnerung(en) gesendet.")
