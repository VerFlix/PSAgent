"""Management-Befehl: Altdaten nach PSAgent 2.0 übernehmen, mit Probelauf (#10)."""

from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from legacy.importer import LegacyImportError, run_import


class Command(BaseCommand):
    help = (
        "Übernimmt psa.db, psa_archiv.db und GAL/ aus einem Ordner in eine leere Datenbank. "
        "Mit --probelauf wird nichts geschrieben; der Bericht ist derselbe. Die Quellen werden "
        "nur lesend geöffnet."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("quelle", help="Ordner mit psa.db, ggf. psa_archiv.db und GAL/")
        parser.add_argument(
            "--probelauf", action="store_true", help="nichts schreiben, nur den Bericht erzeugen"
        )
        parser.add_argument("--bericht", help="Bericht zusätzlich in diese Datei schreiben")

    def handle(self, *args: Any, **options: Any) -> None:
        report_path = Path(options["bericht"]) if options["bericht"] else None
        if report_path is not None and report_path.exists():
            raise CommandError(f"{report_path} gibt es schon; der Bericht überschreibt nichts.")
        try:
            report = run_import(Path(options["quelle"]), dry_run=options["probelauf"])
        except LegacyImportError as error:
            raise CommandError(str(error)) from error

        text = report.render()
        if report_path is not None:
            report_path.write_text(text, encoding="utf-8")
        self.stdout.write(text)
