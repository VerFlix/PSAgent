"""Export der Teileliste als CSV oder PDF (Parität 11) – ohne Personendaten.

Je Bauteil eine Zeile mit den 9 Feldern, nächster Prüfung, Status, „keine PSA“ und GAL.
Umfang: alle oder markierte Teile, mit oder ohne ausgesonderte.

- CSV mit Semikolon und UTF-8 mit BOM, damit Excel Umlaute richtig liest. Zellen, die mit
  `=`, `+`, `-`, `@` oder einem Steuerzeichen beginnen, bekommen ein `'` davor; sonst würde
  eine Tabellenkalkulation eingeschleuste Formeln ausführen (CSV-Injection).
- PDF mit Typst wie im Altsystem. Die Daten gehen als JSON in die Vorlage und werden dort
  nur als Text gesetzt; Feldinhalte werden nie als Typst-Code ausgewertet.
"""

import csv
import io
import json
from collections.abc import Iterable
from datetime import date
from pathlib import Path
from typing import Any

import typst
from django.conf import settings
from django.db.models import Prefetch, QuerySet

from inspections.services import next_inspection_due
from inventory.models import Component, Part, PartStatus

COLUMNS = (
    ("teil", "Teil-Nr."),
    ("name", "Teil"),
    ("kind", "Art"),
    ("position", "Bauteil"),
    ("designation", "Produktbezeichnung"),
    ("standard", "gem. EN"),
    ("model_name", "Produktname"),
    ("manufacturer", "Hersteller"),
    ("year_of_manufacture", "Herstellungsjahr"),
    ("purchase_date", "Kaufdatum"),
    ("first_use_date", "Datum 1. Einsatz"),
    ("ei", "EI"),
    ("serial_number", "Seriennummer"),
    ("next_due", "nächste Prüfung"),
    ("status", "Status"),
    ("no_ppe", "keine PSA"),
    ("gal", "GAL"),
)
TEMPLATE = Path(settings.BASE_DIR) / "templates" / "typst" / "teileliste.typ"
DANGEROUS_START = ("=", "+", "-", "@", "\t", "\r")


def parts_for_export(ids: Iterable[int] | None, *, with_retired: bool) -> list[Part]:
    """Die Teile in Listenreihenfolge; `ids=None` heißt alle."""
    parts: QuerySet[Part] = Part.objects.prefetch_related(
        Prefetch("components", queryset=Component.objects.order_by("position")),
        "events",
        "documents",
    ).order_by("name", "pk")
    if ids is not None:
        parts = parts.filter(pk__in=list(ids))
    return [part for part in parts if with_retired or part.status() != PartStatus.RETIRED]


def as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "ja" if value else "nein"
    if isinstance(value, date):
        return f"{value:%d.%m.%Y}"
    return str(value)


def rows(parts: Iterable[Part]) -> list[dict[str, str]]:
    """Je Bauteil eine Zeile, alle Werte als Text."""
    result = []
    for part in parts:
        status = part.status()
        no_ppe = part.has_no_ppe()
        due = None if no_ppe else next_inspection_due(part)
        gal = ", ".join(document.title for document in part.documents.all())
        for component in part.components.all():
            values: dict[str, Any] = {
                "teil": part.pk,
                "name": part.name,
                "kind": part.get_kind_display(),
                "position": component.position,
                "next_due": due,
                "status": status.label,
                "no_ppe": no_ppe,
                "gal": gal,
            }
            for name in (
                "designation",
                "standard",
                "model_name",
                "manufacturer",
                "year_of_manufacture",
                "purchase_date",
                "first_use_date",
                "ei",
                "serial_number",
            ):
                values[name] = getattr(component, name)
            result.append({key: as_text(values[key]) for key, _ in COLUMNS})
    return result


def _cell(value: str) -> str:
    return f"'{value}" if value.startswith(DANGEROUS_START) else value


def to_csv(data: list[dict[str, str]]) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";", lineterminator="\r\n")
    writer.writerow([label for _, label in COLUMNS])
    for row in data:
        writer.writerow([_cell(row[key]) for key, _ in COLUMNS])
    return buffer.getvalue().encode("utf-8-sig")


def to_pdf(data: list[dict[str, str]], *, title: str, stand: str) -> bytes:
    payload = {
        "title": title,
        "stand": stand,
        "columns": [label for _, label in COLUMNS],
        "rows": [[row[key] for key, _ in COLUMNS] for row in data],
    }
    result = typst.compile(TEMPLATE, format="pdf", sys_inputs={"data": json.dumps(payload)})
    assert isinstance(result, bytes)  # noqa: S101 – ein Dokument, eine Datei
    return result
