"""Verwaltungsbefehl: Erinnerungen bei Überfälligkeit und Sammelmail senden (täglicher Timer).

Erinnerungen an Entleiher (#72) und die Sammelmail an Vereinsadressen (#128) haben je einen
eigenen Schalter. Die Ausgabe nennt Vorgänge nur mit Nummer und Empfänger nur als Anzahl, damit
im Protokoll des Timers keine Namen oder Adressen stehen. Scheitert die Sammelmail, endet der
Befehl mit Fehler; das meldet der Herzschlag des Timers an das Monitoring (#24).
"""

from datetime import date
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from lending import digest, reminders


def _bookings(number: int, *, dative: bool = False) -> str:
    if number == 1:
        return "1 Vorgang"
    return f"{number} Vorgängen" if dative else f"{number} Vorgänge"


class Command(BaseCommand):
    help = "Sendet fällige E-Mail-Erinnerungen und die Sammelmail zu überfälligen Ausleihen."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--probelauf", action="store_true", help="Nur anzeigen, was gesendet würde."
        )
        parser.add_argument(
            "--stichtag", type=date.fromisoformat, help="Datum statt heute (JJJJ-MM-TT)."
        )

    def handle(self, *args: Any, **options: Any) -> None:
        dry_run = options["probelauf"]
        on = options["stichtag"]
        self._reminders(on, dry_run)
        self._digest(on, dry_run)

    def _reminders(self, on: date | None, dry_run: bool) -> None:
        if not reminders.enabled():
            self.stdout.write("Erinnerungen sind für alle ausgeschaltet.")
            return
        result = reminders.run(on, dry_run=dry_run)
        for entry in result:
            self.stdout.write(f"  Ausleihvorgang #{entry.booking.pk}: {entry.number}. Erinnerung")
        if dry_run:
            self.stdout.write(f"Probelauf: {len(result)} Erinnerung(en) würden gesendet.")
        else:
            self.stdout.write(f"{len(result)} Erinnerung(en) gesendet.")

    def _digest(self, on: date | None, dry_run: bool) -> None:
        try:
            result = digest.run(on, dry_run=dry_run)
        except digest.DigestError as error:
            raise CommandError(f"Sammelmail: {error}") from error
        if result.sent:
            self.stdout.write(
                f"Sammelmail: {_bookings(result.bookings)} "
                f"an {result.recipients} Adresse(n) gesendet."
            )
        elif dry_run and result.bookings:
            self.stdout.write(
                f"Probelauf: Sammelmail mit {_bookings(result.bookings, dative=True)} "
                f"an {result.recipients} Adresse(n)."
            )
        else:
            self.stdout.write(f"Sammelmail: nicht gesendet ({result.reason}).")
