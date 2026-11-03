"""Persönliche QR-Karte für die Theke (#126), im Scheckkartenformat auf A4.

Der QR-Code enthält nur die zufällige Kennung (`accounts.counter`), keinen Namen. Auf der Karte
steht der Name, damit sie sich zuordnen lässt; ohne PIN und Theke-Gerät öffnet sie nichts.
Gesetzt wird mit Typst wie die Etiketten; Werte gehen nur als Text hinein.
"""

import json
from pathlib import Path

import typst
from django.conf import settings

from accounts.models import User
from inventory.labels import qr_svg

TEMPLATE = Path(settings.BASE_DIR) / "templates" / "typst" / "theke_karte.typ"


def counter_card(user: User, code: str) -> bytes:
    data = json.dumps(
        {
            "name": user.get_full_name() or user.get_username(),
            "club": settings.PSAGENT_CLUB_NAME,
            "qr": qr_svg(code),
        }
    )
    result = typst.compile(TEMPLATE, format="pdf", sys_inputs={"data": data})
    assert isinstance(result, bytes)  # noqa: S101 – ein Dokument, eine Datei
    return result
