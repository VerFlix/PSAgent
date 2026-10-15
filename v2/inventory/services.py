"""Anlegen von Teilen, Speichern von GAL-Dokumenten und Erfassen von Vorgängen.

Die Fachregeln, die die Datenbank nicht allein prüfen kann, stehen hier: Ein Produkt hat genau
ein Bauteil, ein System mindestens eines; nach dem Aussondern gibt es keine Vorgänge mehr;
eine Sperre setzt ein nicht gesperrtes Teil voraus. Rechte prüft #12.
"""

import hashlib
from datetime import datetime
from pathlib import PurePath
from typing import Any

from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone

from accounts.models import User
from inventory.models import (
    EVENTS_WITH_COMMENT,
    Component,
    Document,
    EventKind,
    Part,
    PartEvent,
    PartKind,
)


class InventoryError(ValueError):
    """Ein Vorgang verstößt gegen eine Fachregel der Stammdaten."""


def _add_components(part: Part, components: list[dict[str, Any]]) -> None:
    for position, fields in enumerate(components, start=1):
        Component.objects.create(part=part, position=position, **fields)


@transaction.atomic
def create_product(
    component: dict[str, Any],
    *,
    name: str | None = None,
    actor: User | None = None,
    legacy_kind: str = "",
    legacy_id: int | None = None,
) -> Part:
    """Legt ein Produkt mit genau einem Bauteil an; Status „neu, nicht freigegeben“."""
    default_name = " ".join(
        value for value in (component.get("designation"), component.get("model_name")) if value
    )
    part = Part.objects.create(
        kind=PartKind.PRODUCT,
        name=name or default_name or str(component.get("ei", "")),
        created_by=actor,
        legacy_kind=legacy_kind,
        legacy_id=legacy_id,
    )
    _add_components(part, [component])
    return part


@transaction.atomic
def create_system(
    name: str,
    components: list[dict[str, Any]],
    *,
    actor: User | None = None,
    legacy_kind: str = "",
    legacy_id: int | None = None,
) -> Part:
    """Legt ein System mit mindestens einem Bauteil an; Status „neu, nicht freigegeben“."""
    if not components:
        raise InventoryError("Ein System braucht mindestens ein Bauteil.")
    part = Part.objects.create(
        kind=PartKind.SYSTEM,
        name=name,
        created_by=actor,
        legacy_kind=legacy_kind,
        legacy_id=legacy_id,
    )
    _add_components(part, components)
    return part


def store_document_file(
    content: bytes, filename: str, *, title: str, actor: User | None = None
) -> Document:
    """Speichert eine GAL-Datei. Gleicher Inhalt wird nur einmal gespeichert."""
    digest = hashlib.sha256(content).hexdigest()
    existing = Document.objects.filter(sha256=digest).first()
    if existing is not None:
        return existing
    suffix = PurePath(filename).suffix.lower()
    document = Document(title=title, sha256=digest, created_by=actor)
    document.file.save(f"{digest}{suffix}", ContentFile(content), save=False)
    document.save()
    return document


def link_document(url: str, *, title: str, actor: User | None = None) -> Document:
    """Legt einen GAL-Link an oder gibt den vorhandenen mit derselben Adresse zurück."""
    existing = Document.objects.filter(url=url, file="").first()
    if existing is not None:
        return existing
    return Document.objects.create(title=title, url=url, created_by=actor)


@transaction.atomic
def record_event(
    part: Part,
    kind: EventKind,
    *,
    actor: User | None,
    comment: str = "",
    at: datetime | None = None,
) -> PartEvent:
    """Erfasst einen Vorgang am Teil und prüft die Fachregeln dafür.

    Die Zeile des Teils wird gesperrt, damit gleichzeitige Vorgänge nacheinander geprüft werden.
    """
    Part.objects.select_for_update().get(pk=part.pk)
    moment = at or timezone.now()
    if part.is_retired():
        raise InventoryError(f"{part} ist ausgesondert; weitere Vorgänge sind nicht möglich.")
    if kind in EVENTS_WITH_COMMENT and not comment.strip():
        raise InventoryError(f"„{kind.label}“ braucht einen Kommentar.")
    if kind == EventKind.LOCKED and part.is_locked():
        raise InventoryError(f"{part} ist bereits gesperrt.")
    if kind == EventKind.UNLOCKED and not part.is_locked():
        raise InventoryError(f"{part} ist nicht gesperrt.")
    if kind == EventKind.RELEASED and part.events.filter(kind=EventKind.RELEASED).exists():
        raise InventoryError(f"{part} ist bereits freigegeben.")
    return PartEvent.objects.create(part=part, kind=kind, actor=actor, comment=comment, at=moment)
