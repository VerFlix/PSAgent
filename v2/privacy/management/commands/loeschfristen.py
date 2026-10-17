"""Verwaltungsbefehl: Personendaten nach Ablauf der Löschfrist löschen (für den täglichen Timer).

Nennt Entleiher nur mit Nummer, damit im Protokoll des Timers keine Namen stehen.
"""

from datetime import date
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from lending.models import Borrower
from privacy import services


class Command(BaseCommand):
    help = "Löscht Personendaten aller Entleiher, deren Löschfrist abgelaufen ist (#63)."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--probelauf", action="store_true", help="Nur anzeigen, was gelöscht würde."
        )
        parser.add_argument(
            "--stichtag", type=date.fromisoformat, help="Datum statt heute (JJJJ-MM-TT)."
        )
        parser.add_argument(
            "--entleiher",
            type=int,
            metavar="NR",
            help="Nur diesen Entleiher sofort löschen, z. B. auf Antrag (Art. 17 DSGVO).",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        if options["entleiher"] is not None:
            self._erase_one(options["entleiher"])
            return
        dry_run = options["probelauf"]
        due = services.run_retention(options["stichtag"], dry_run=dry_run)
        for entry in due:
            self.stdout.write(
                f"  Entleiher #{entry.borrower.pk}, letzte Aktivität {entry.last_activity:%Y}"
            )
        if dry_run:
            self.stdout.write(f"Probelauf: {len(due)} Entleiher würden gelöscht.")
        else:
            self.stdout.write(f"{len(due)} Entleiher gelöscht.")

    def _erase_one(self, number: int) -> None:
        borrower = Borrower.objects.filter(pk=number).first()
        if borrower is None:
            raise CommandError(f"Entleiher #{number} gibt es nicht.")
        try:
            services.erase_borrower(borrower, actor=None)
        except services.PrivacyError as error:
            raise CommandError(f"Entleiher #{number}: {error}") from error
        self.stdout.write(f"Entleiher #{number} gelöscht.")
