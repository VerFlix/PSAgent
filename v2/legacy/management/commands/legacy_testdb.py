"""Management-Befehl: künstliche Altsystem-Datenbank erzeugen (Testdaten-Generator)."""

from datetime import date
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from legacy.schema import VARIANTS
from legacy.testdata import DEFAULT_REFERENCE_DATE, build_legacy_db


class Command(BaseCommand):
    help = (
        "Erzeugt künstliche Testdaten im Format des Altsystems (psa.db, ggf. psa_archiv.db, "
        "GAL/) in einem leeren Ordner. Liest und überschreibt keine vorhandenen Dateien."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--variant", default="s7", help="Schema-Variante, z. B. s7")
        parser.add_argument("--seed", type=int, default=1, help="Startwert für den Zufall")
        parser.add_argument("--out", help="Zielordner (wird angelegt, muss leer sein)")
        parser.add_argument(
            "--stichtag",
            default=DEFAULT_REFERENCE_DATE.isoformat(),
            help="Bezugsdatum für Fristen, Format JJJJ-MM-TT",
        )
        parser.add_argument(
            "--ohne-randfaelle", action="store_true", help="nur saubere Daten, keine Randfälle"
        )
        parser.add_argument(
            "--liste", action="store_true", help="verfügbare Schema-Varianten anzeigen"
        )

    def handle(self, *args: Any, **options: Any) -> None:
        if options["liste"]:
            for name, variant in VARIANTS.items():
                archive = " (mit Archiv)" if variant.has_archive else ""
                self.stdout.write(f"{name:18} {variant.description}{archive}")
            return

        if not options["out"]:
            raise CommandError("Bitte mit --out einen Zielordner angeben.")
        if options["variant"] not in VARIANTS:
            raise CommandError(
                f"Unbekannte Variante {options['variant']!r}. Verfügbar: {', '.join(VARIANTS)}"
            )
        try:
            reference_date = date.fromisoformat(options["stichtag"])
        except ValueError as error:
            raise CommandError("--stichtag erwartet ein Datum im Format JJJJ-MM-TT.") from error

        try:
            result = build_legacy_db(
                Path(options["out"]),
                options["variant"],
                options["seed"],
                edge_cases=not options["ohne_randfaelle"],
                reference_date=reference_date,
            )
        except FileExistsError as error:
            raise CommandError(str(error)) from error

        self.stdout.write(f"Variante {result.variant.name}: {result.variant.description}")
        self.stdout.write(f"  {result.main_db}")
        if result.archive_db:
            self.stdout.write(f"  {result.archive_db}")
        self.stdout.write(f"  {result.gal_dir}/")
