"""Verwaltungsbefehl: Prüfwerkzeug (#15, #19) über Kette, Zeugen, Prüfberichte und Zertifikate.

Die täglich festgehaltenen Köpfe (`WitnessHead`) und ihre Zeitstempel prüft der Befehl immer
mit, ebenso alle signierten Prüfberichte. Zusätzlich:

- `--kopf NUMMER:SHA256`: ein Kopf, z. B. abgeschrieben aus der Zeugen-Mail (mehrfach möglich),
- `--zeugen DATEI_ODER_ORDNER`: gespeicherte Zeugen-Mails (`.eml`) oder Kopf-Dateien von Unraid
  (`.txt`), samt Zeitstempel (mehrfach möglich),
- `--fingerabdruecke DATEI`: Liste der Signaturzertifikate außerhalb des Servers (z. B. vom
  USB-Stick); jedes Zertifikat in PSAgent muss darauf stehen.

Ergebnis und Rückgabewert: unverändert (0), verändert (1), nicht prüfbar (2). Die Meldungen
nennen nur Tabellen, Nummern und Fingerabdrücke, keine Inhalte.
Ablauf: `docs/runbooks/pruefwerkzeug.md`.
"""

from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from core import audit, chain
from inspections.reports import Verdict


class Command(BaseCommand):
    help = "Prüft Hash-Kette, festgehaltene Köpfe, Zeugen-Dateien, Prüfberichte und Zertifikate."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--kopf",
            action="append",
            default=[],
            metavar="NUMMER:SHA256",
            help="Früher festgehaltener Kettenkopf, z. B. aus der Zeugen-Mail; mehrfach möglich.",
        )
        parser.add_argument(
            "--zeugen",
            action="append",
            default=[],
            metavar="DATEI_ODER_ORDNER",
            help="Zeugen-Mails (.eml) oder Kopf-Dateien (.txt); Ordner rekursiv; mehrfach möglich.",
        )
        parser.add_argument(
            "--fingerabdruecke",
            metavar="DATEI",
            help="Textdatei mit den SHA-256-Fingerabdrücken der Signaturzertifikate.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        try:
            heads = [chain.Head.parse(text) for text in options["kopf"]]
            result = audit.run(
                heads=heads,
                witness_paths=options["zeugen"],
                fingerprints=options["fingerabdruecke"],
            )
        except (ValueError, audit.AuditInputError) as error:
            raise CommandError(str(error)) from error
        current = chain.head()
        self.stdout.write(f"Kettenkopf: {current}" if current else "Die Kette ist leer.")
        for title, lines, stream in (
            ("Verändert", result.findings, self.stderr),
            ("Nicht prüfbar", result.unchecked, self.stderr),
            ("Geprüft", result.confirmed, self.stdout),
            ("Hinweise", result.notes, self.stdout),
        ):
            if lines:
                stream.write(f"{title}:")
                for line in lines:
                    stream.write(f"  {line}")
        verdict = result.verdict
        self.stdout.write(f"Ergebnis: {verdict}.")
        if verdict == Verdict.CHANGED:
            raise CommandError(f"Verändert: {len(result.findings)} Befund(e).", returncode=1)
        if verdict == Verdict.UNCHECKABLE:
            raise CommandError(
                f"Nicht prüfbar: {len(result.unchecked)} Grund/Gründe.", returncode=2
            )
