"""Verwaltungsbefehl: Erinnerungen bei Überfälligkeit und Sammelmails senden (täglicher Timer).

Erinnerungen an Entleiher (#72), die Sammelmail zu überfälligen Ausleihen (#128) und die
Sammelmail zu neuen Sperren (#141) haben je einen eigenen Schalter. Die Ausgabe nennt Vorgänge
nur mit Nummer und Empfänger nur als Anzahl, damit im Protokoll des Timers keine Namen oder
Adressen stehen. Scheitert eine Sammelmail, läuft die andere trotzdem, und der Befehl endet mit
Fehler; das meldet der Herzschlag des Timers an das Monitoring (#24).
"""

from datetime import date
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from inventory import lock_digest
from lending import digest, reminders
from mailer.digests import DigestError


def _bookings(number: int, *, dative: bool = False) -> str:
    if number == 1:
        return "1 Vorgang"
    return f"{number} Vorgängen" if dative else f"{number} Vorgänge"


class Command(BaseCommand):
    help = "Sendet fällige E-Mail-Erinnerungen und die Sammelmails (überfällig, neue Sperren)."

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
        errors = []
        for label, step in (
            ("Sammelmail", self._digest),
            ("Sammelmail zu Sperren", self._lock_digest),
        ):
            try:
                step(on, dry_run)
            except DigestError as error:
                errors.append(f"{label}: {error}")
        if errors:
            raise CommandError(" ".join(errors))

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
        result = digest.run(on, dry_run=dry_run)
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

    def _lock_digest(self, on: date | None, dry_run: bool) -> None:
        result = lock_digest.run(on, dry_run=dry_run)
        if result.sent:
            self.stdout.write(
                f"Sammelmail zu Sperren: {result.locks} Sperre(n) "
                f"an {result.recipients} Adresse(n) gesendet."
            )
        elif dry_run and result.locks:
            self.stdout.write(
                f"Probelauf: Sammelmail zu Sperren mit {result.locks} Sperre(n) "
                f"an {result.recipients} Adresse(n)."
            )
        else:
            self.stdout.write(f"Sammelmail zu Sperren: nicht gesendet ({result.reason}).")
