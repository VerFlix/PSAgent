"""PDF-Dokumente je Teil (Parität 5): Einsatzdokumentation, PD und Haftungsausschluss.

Wie im Altsystem (`Typst_Vorlagen/`) leere Formulare zum Ausfüllen auf Papier. Unterschiede:

- Name und Briefkopf/Stempel des Sachkundigen kommen aus dem Konto der erzeugenden Person
  (`User.stamp`), der Besitzer (Verein) aus `PSAGENT_CLUB_NAME`; nichts davon steht in der
  Vorlage.
- PD und Haftungsausschluss tragen den Stempel und erzeugt darum nur, wer gültige Sachkunde hat
  (Recht „PD und Haftungsausschluss erzeugen“). Die Einsatzdokumentation bleibt bei „Teile
  anlegen, …, Einsatzdokumentation erzeugen“ (ab Trainer).
- Alle Bauteile stehen in einer Tabelle; das Altsystem setzte höchstens drei.

Die Daten gehen als JSON in die Vorlage und werden dort nur als Text gesetzt; Feldinhalte und
Stempel werden nie als Typst-Code ausgewertet.
"""

import json
from enum import StrEnum
from pathlib import Path
from typing import Any

import typst
from django.conf import settings
from django.utils import timezone

from accounts.models import User
from inventory.export import as_text
from inventory.models import Part

TEMPLATES = Path(settings.BASE_DIR) / "templates" / "typst"
FIELDS = (
    ("designation", "Produktbezeichnung"),
    ("standard", "gem. EN"),
    ("model_name", "Produktname"),
    ("manufacturer", "Hersteller"),
    ("year_of_manufacture", "Herstellungsjahr"),
    ("purchase_date", "Kaufdatum"),
    ("first_use_date", "Datum 1. Einsatz"),
    ("ei", "Einzelidentifikation"),
    ("serial_number", "Seriennummer"),
)


class DocumentKind(StrEnum):
    USAGE_LOG = "einsatzdokumentation"
    DATA_SHEET = "pd"
    DISCLAIMER = "haftungsausschluss"


# Dateiname wie im Altsystem: „<Teil>_<Dokument>.pdf“.
FILE_SUFFIX = {
    DocumentKind.USAGE_LOG: "Einsatzdokumentation",
    DocumentKind.DATA_SHEET: "PD",
    DocumentKind.DISCLAIMER: "Haftungsausschluss",
}


def payload(kind: DocumentKind, part: Part, *, user: User) -> dict[str, Any]:
    """Alle Werte für die Vorlage als Text."""
    components = part.components.order_by("position")
    return {
        "part": part.name,
        "system": part.kind == "system",
        "stand": f"{timezone.localdate():%d.%m.%Y}",
        "club": settings.PSAGENT_CLUB_NAME,
        "expert": user.get_full_name() or user.get_username(),
        "stamp": user.stamp.splitlines() if kind != DocumentKind.USAGE_LOG else [],
        "columns": ["Nr."] + [label for _, label in FIELDS],
        "rows": [
            [str(component.position)]
            + [as_text(getattr(component, name)) or "–" for name, _ in FIELDS]
            for component in components
        ],
    }


def render(kind: DocumentKind, part: Part, *, user: User) -> bytes:
    data = json.dumps(payload(kind, part, user=user))
    result = typst.compile(TEMPLATES / f"{kind}.typ", format="pdf", sys_inputs={"data": data})
    assert isinstance(result, bytes)  # noqa: S101 – ein Dokument, eine Datei
    return result


def filename(kind: DocumentKind, part: Part) -> str:
    """Wie im Altsystem: System nach Namen, Produkt nach EI; dahinter das Dokument.

    Nur Buchstaben, Ziffern, Punkt und Bindestrich; alles andere wird „_“.
    """
    first = part.components.order_by("position").first()
    label = part.name if part.kind == "system" or first is None else first.ei
    safe = "".join(char if char.isalnum() or char in ".-" else "_" for char in label)
    return f"{safe.strip('_.') or 'Teil'}_{FILE_SUFFIX[kind]}.pdf"
