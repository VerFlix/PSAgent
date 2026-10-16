"""Vollständige Sicherung als ZIP (Parität 11): alle Daten lesbar, ohne Werkzeug.

Inhalt:
- `daten.json`: alle Tabellen von PSAgent im JSON-Format von Django (wie `dumpdata`).
- `dateien/`: die GAL-PDFs.
- `pruefsummen.sha256`: SHA-256 jeder Datei im ZIP, im Format von `sha256sum`.

Ersetzt nicht das verschlüsselte Datenbank-Backup (#23). Zugangsdaten bleiben draußen:
Passwort-Hashes, Passkeys, Einrichtungslinks und Vereinsgeräte; dazu Sitzungen und
Fehlversuche. So verbreitet eine heruntergeladene Sicherung keine Anmeldedaten.
"""

import hashlib
import io
import json
import zipfile
from collections.abc import Iterator
from typing import Any

from django.apps import apps
from django.core import serializers
from django.db import models

from inventory.models import Document

APPS = ("accounts", "inventory", "inspections", "lending", "emergency", "legacy")
LEFT_OUT = {
    "accounts.passkey",
    "accounts.enrollmenttoken",
    "accounts.clubdevice",
}
USER_FIELDS_LEFT_OUT = ("password", "session_epoch")


def _models() -> Iterator[type[models.Model]]:
    for label in APPS:
        for model in apps.get_app_config(label).get_models():
            if model._meta.label_lower not in LEFT_OUT:
                yield model


def data() -> list[dict[str, Any]]:
    """Alle Datensätze als Liste wie `dumpdata`, ohne Zugangsdaten."""
    records: list[dict[str, Any]] = []
    for model in _models():
        objects = model._default_manager.order_by("pk")
        records.extend(json.loads(serializers.serialize("json", objects)))
    for record in records:
        if record["model"] == "accounts.user":
            for field in USER_FIELDS_LEFT_OUT:
                record["fields"].pop(field, None)
    return records


def build() -> bytes:
    """Das ZIP als Bytes; GAL-Dateien unter ihrem SHA-256."""
    files: dict[str, bytes] = {
        "daten.json": json.dumps(data(), ensure_ascii=False, indent=1).encode("utf-8")
    }
    for document in Document.objects.exclude(file="").order_by("pk"):
        with document.file.open("rb") as handle:
            files[f"dateien/{document.sha256}.pdf"] = handle.read()
    sums = "".join(
        f"{hashlib.sha256(content).hexdigest()}  {name}\n" for name, content in files.items()
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
        archive.writestr("pruefsummen.sha256", sums)
    return buffer.getvalue()
