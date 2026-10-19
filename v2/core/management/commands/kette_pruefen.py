"""Verwaltungsbefehl: Hash-Kette prüfen (#15), auch gegen festgehaltene und verschickte Köpfe.

Die täglich festgehaltenen Köpfe (`WitnessHead`) und ihre Zeitstempel prüft der Befehl immer
mit; Köpfe aus den Zeugen-Mails lassen sich mit `--kopf` zusätzlich angeben.

Die Meldungen nennen nur Tabellen und Nummern, keine Inhalte.
"""

from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from core import chain, witness


class Command(BaseCommand):
    help = "Prüft die Hash-Kette über alle Vorgänge."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--kopf",
            action="append",
            default=[],
            metavar="NUMMER:SHA256",
            help="Früher festgehaltener Kettenkopf, z. B. aus der Zeugen-Mail; mehrfach möglich.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        try:
            known = [chain.Head.parse(text) for text in options["kopf"]]
        except ValueError as error:
            raise CommandError(str(error)) from error
        current = chain.head()
        self.stdout.write(f"Kettenkopf: {current}" if current else "Die Kette ist leer.")
        problems = chain.verify([*known, *witness.known_heads()]) + witness.verify()
        if problems:
            for problem in problems:
                self.stderr.write(f"  {problem}")
            raise CommandError(f"Hash-Kette: {len(problems)} Befund(e).")
        self.stdout.write("Kette unverändert.")
