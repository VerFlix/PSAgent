"""Prüfprotokoll einer abgeschlossenen Runde als PDF (Parität 8, #38 Schritt 1).

Festlegungen (Felix, 21.10.2026):

- Je Teil die Stammdaten aller Bauteile (EI, Hersteller, Modell, Herstellungsjahr,
  Seriennummer), dazu Ergebnis, Kommentar und nächste Prüfung. So ist das Protokoll ohne
  PSAgent verständlich.
- Das PDF entsteht einmal beim Abschließen und wird gespeichert (`RoundProtocol`). Es trägt den
  Kettenkopf von diesem Moment; signiert werden muss genau diese Datei.
- Unten auf der letzten Seite steht ein leeres Signaturfeld `SIGNATURE_FIELD`. Signier-Tool und
  Acrobat setzen die sichtbare Unterschrift dort hinein.
- Herunterladen darf nur der Prüfer der Runde.

Typst setzt das PDF (Daten als JSON, nur als Text gesetzt), pyHanko fügt das Signaturfeld als
Nachtrag an. Das ist reiner PDF-Aufbau, keine Kryptografie.
"""

import hashlib
import json
from datetime import date, datetime
from io import BytesIO
from pathlib import Path
from typing import Any

import typst
from django.conf import settings
from django.utils import timezone
from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
from pyhanko.sign.fields import SigFieldSpec, append_signature_field

from accounts.models import Qualification
from core.chain import Head
from inspections.models import InspectionRound, Outcome

TEMPLATE = Path(settings.BASE_DIR) / "templates" / "typst" / "pruefprotokoll.typ"
SIGNATURE_FIELD = "Sachkundiger"
# Signaturfeld auf der letzten Seite (A4), in cm vom oberen linken Seitenrand.
FIELD_CM = {"left": 1.5, "top": 24.6, "width": 9.0, "height": 3.2}
PAGE_HEIGHT_CM = 29.7
POINTS_PER_CM = 72 / 2.54

STATEMENT = (
    "Mit meiner Signatur bestätige ich als PSA-Sachkundige*r, die aufgeführten Teile nach "
    "Gebrauchsanleitung des Herstellers geprüft zu haben. Die Signatur deckt dieses Protokoll "
    "und den genannten Kettenkopf ab."
)


def _date(value: Any) -> str:
    return f"{value:%d.%m.%Y}" if value else "–"


def _moment(value: datetime) -> str:
    return f"{timezone.localtime(value):%d.%m.%Y %H:%M}"


def _text(value: Any) -> str:
    """Feldwert als Text; leer wird „–“ (wie `inventory.export.as_text`, ohne Ringimport)."""
    if value is None or value == "":
        return "–"
    return _date(value) if isinstance(value, date) else str(value)


def payload(
    round_: InspectionRound, *, head: Head, closed_at: datetime, content_sha256: str
) -> dict[str, Any]:
    """Alle Werte für die Vorlage als Text."""
    expert = round_.inspector
    assert expert is not None  # noqa: S101 – nur Runden mit Konto bekommen ein Protokoll
    qualification = (
        Qualification.valid_at(closed_at).filter(user=expert).order_by("-valid_until").first()
    )
    parts = []
    for result in round_.current_results().select_related("part").order_by("part__name", "pk"):
        part = result.part
        parts.append(
            {
                "name": part.name,
                "system": part.kind == "system",
                "outcome": Outcome(result.outcome).label,
                "comment": result.comment or "–",
                "next_due": _date(result.next_due),
                "components": [
                    [
                        str(component.position),
                        _text(component.ei),
                        _text(component.manufacturer),
                        _text(component.model_name),
                        _text(component.year_of_manufacture),
                        _text(component.serial_number),
                    ]
                    for component in part.components.order_by("position")
                ],
            }
        )
    return {
        "title": f"Prüfprotokoll Runde {round_.pk}",
        "club": settings.PSAGENT_CLUB_NAME or "–",
        "round": str(round_.pk),
        "expert": expert.get_full_name() or expert.get_username(),
        "stamp": expert.stamp.splitlines(),
        "qualification_until": _date(qualification.valid_until if qualification else None),
        "started": _moment(round_.started_at),
        "closed": _moment(closed_at),
        "parts": parts,
        "content_sha256": content_sha256,
        "chain_seq": str(head.seq),
        "chain_hash": head.hash,
        "statement": STATEMENT,
        "field": FIELD_CM,
    }


def field_box() -> tuple[int, int, int, int]:
    """Signaturfeld in PDF-Punkten (Ursprung unten links): links, unten, rechts, oben."""
    left = FIELD_CM["left"] * POINTS_PER_CM
    top = (PAGE_HEIGHT_CM - FIELD_CM["top"]) * POINTS_PER_CM
    return (
        round(left),
        round(top - FIELD_CM["height"] * POINTS_PER_CM),
        round(left + FIELD_CM["width"] * POINTS_PER_CM),
        round(top),
    )


def render(
    round_: InspectionRound, *, head: Head, closed_at: datetime, content_sha256: str
) -> bytes:
    """Setzt das Protokoll und hängt das leere Signaturfeld an."""
    data = json.dumps(
        payload(round_, head=head, closed_at=closed_at, content_sha256=content_sha256)
    )
    pdf = typst.compile(TEMPLATE, format="pdf", sys_inputs={"data": data})
    assert isinstance(pdf, bytes)  # noqa: S101 – ein Dokument, eine Datei
    writer = IncrementalPdfFileWriter(BytesIO(pdf))
    append_signature_field(
        writer, SigFieldSpec(sig_field_name=SIGNATURE_FIELD, on_page=-1, box=field_box())
    )
    out = BytesIO()
    writer.write(out)  # type: ignore[no-untyped-call]
    return out.getvalue()


def sha256(pdf: bytes) -> str:
    return hashlib.sha256(pdf).hexdigest()


def filename(round_: InspectionRound) -> str:
    return f"Pruefprotokoll_Runde_{round_.pk}.pdf"
