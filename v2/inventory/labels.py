"""QR-Etiketten (#14): je Bauteil ein Etikett mit QR-Code, EI und Bezeichnung.

Format: A4-Bogen mit 65 Etiketten 38 x 21,2 mm in 5 Spalten und 13 Zeilen (z. B. Avery
Zweckform 3666/L7651). Mit `start` beginnt der Druck auf einem angebrochenen Bogen.

Der QR-Code enthält nur den Link `<PSAGENT_BASE_URL>/q/<EI>`, keine Personendaten. Die EI
steht ohnehin am Teil und bleibt beim Import aus dem Altsystem gleich; der Link führt nach der
Anmeldung zum Teil (`inventory.views.scan_target`). Den QR-Code erzeugt `segno` als SVG; Typst
setzt ihn als Bild, Feldinhalte nur als Text.
"""

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any
from urllib.parse import quote

import segno
import typst
from django.conf import settings
from django.urls import reverse

from inventory.models import Part

TEMPLATE = Path(settings.BASE_DIR) / "templates" / "typst" / "etiketten.typ"
PER_SHEET = 65


def target_url(ei: str) -> str:
    """Link im QR-Code; die EI wird vollständig kodiert (Leerzeichen, Schrägstrich)."""
    base = str(settings.PSAGENT_BASE_URL).rstrip("/")
    return base + reverse("scan_target", kwargs={"ei": "EI"}).replace("EI", quote(ei, safe=""))


def qr_svg(data: str) -> str:
    """QR-Code als SVG mit 2 Modulen Ruhezone (Fehlerkorrektur M, verträgt kleine Kratzer)."""
    code = segno.make(data, error="m", micro=False)
    svg: str = code.svg_inline(border=2, dark="black", omitsize=True)
    return svg


def entries(parts: Iterable[Part]) -> list[dict[str, Any]]:
    """Je Bauteil ein Etikett; ausgesonderte Teile bekommen keins."""
    result = []
    for part in parts:
        if part.is_retired():
            continue
        components = list(part.components.order_by("position"))
        for component in components:
            title = part.name if part.kind == "system" else component.designation or part.name
            detail = component.designation if part.kind == "system" else ""
            result.append(
                {
                    "ei": component.ei,
                    "title": title,
                    "detail": detail or "",
                    "qr": qr_svg(target_url(component.ei)),
                }
            )
    return result


def render(labels: list[dict[str, Any]], *, start: int = 1) -> bytes:
    """PDF mit den Etiketten; `start` = erstes freies Etikett auf dem Bogen (1–65)."""
    start = start if 1 <= start <= PER_SHEET else 1
    data = json.dumps({"start": start, "labels": labels})
    result = typst.compile(TEMPLATE, format="pdf", sys_inputs={"data": data})
    assert isinstance(result, bytes)  # noqa: S101 – ein Dokument, eine Datei
    return result
