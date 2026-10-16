"""Anlegen von Teilen, Speichern von GAL-Dokumenten und Erfassen von Vorgängen.

Die Fachregeln, die die Datenbank nicht allein prüfen kann, stehen hier: Ein Produkt hat genau
ein Bauteil, ein System mindestens eines; nach dem Aussondern gibt es keine Vorgänge mehr;
eine Sperre setzt ein nicht gesperrtes Teil voraus.

Was die PSA betrifft, braucht eine gültige Sachkunde (CLAUDE.md, Fachregeln): Entsperren,
Aussondern, „keine PSA“ setzen oder aufheben und die PSA-relevanten Felder EI, Herstellungsjahr
und Datum 1. Einsatz ändern. Freigeben geht nur über eine PSA-Prüfung. Sperren darf jede Rolle ab
Verleiher; Teile anlegen und pflegen ab Trainer/Materialwart (Rechtetabelle, `accounts.rights`).
"""

import hashlib
from datetime import datetime
from typing import TYPE_CHECKING, Any

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.validators import URLValidator
from django.db import transaction
from django.db.models import Q, QuerySet
from django.utils import timezone

from accounts.models import User
from accounts.rights import Right, require
from inventory.models import (
    EVENTS_WITH_COMMENT,
    Component,
    Document,
    EventKind,
    Part,
    PartEvent,
    PartKind,
    normalize_ei,
)

if TYPE_CHECKING:
    from inspections.models import InspectionResult

PDF_MAGIC = b"%PDF-"

# Recht je Vorgang (Rechtetabelle). Nur Sperren braucht keine Sachkunde.
EVENT_RIGHTS = {
    EventKind.RELEASED: Right.PPE_FIELDS,
    EventKind.NO_PPE_SET: Right.PPE_FIELDS,
    EventKind.NO_PPE_CLEARED: Right.PPE_FIELDS,
    EventKind.LOCKED: Right.LOCK,
    EventKind.UNLOCKED: Right.INSPECT,
    EventKind.RETIRED: Right.INSPECT,
}

# Bauteilfelder, die geändert werden dürfen, und die davon PSA-relevanten.
COMPONENT_FIELDS = (
    "designation",
    "standard",
    "model_name",
    "manufacturer",
    "year_of_manufacture",
    "purchase_date",
    "first_use_date",
    "ei",
    "serial_number",
)
PPE_COMPONENT_FIELDS = ("ei", "year_of_manufacture", "first_use_date")
_validate_url = URLValidator(schemes=["http", "https"])


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
    require(actor, Right.MANAGE_PARTS)
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
    require(actor, Right.MANAGE_PARTS)
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
    """Speichert eine GAL-Datei. Gleicher Inhalt wird nur einmal gespeichert.

    Angenommen werden nur PDFs (erkannt am Dateianfang, nicht an der Endung); gespeichert wird
    immer als `.pdf`, damit keine hochgeladene Datei als HTML oder Skript ausgeliefert wird.
    `filename` dient nur der Fehlermeldung.
    """
    require(actor, Right.MANAGE_PARTS)
    if not content.startswith(PDF_MAGIC):
        raise InventoryError(f"{filename} ist kein PDF; GAL-Dateien müssen PDFs sein.")
    digest = hashlib.sha256(content).hexdigest()
    existing = Document.objects.filter(sha256=digest).first()
    if existing is not None:
        return existing
    document = Document(title=title, sha256=digest, created_by=actor)
    document.file.save(f"{digest}.pdf", ContentFile(content), save=False)
    document.save()
    return document


def link_document(url: str, *, title: str, actor: User | None = None) -> Document:
    """Legt einen GAL-Link an oder gibt den vorhandenen mit derselben Adresse zurück.

    Nur http- und https-Adressen; `javascript:` und Ähnliches wird abgewiesen.
    """
    require(actor, Right.MANAGE_PARTS)
    try:
        _validate_url(url)
    except ValidationError as error:
        raise InventoryError(f"Ungültiger Link: {url!r} (nur http und https).") from error
    existing = Document.objects.filter(url=url, file="").first()
    if existing is not None:
        return existing
    return Document.objects.create(title=title, url=url, created_by=actor)


@transaction.atomic
def rename_part(part: Part, name: str, *, actor: User | None) -> Part:
    """Ändert die Bezeichnung eines Teils (Parität 3); ab Trainer/Materialwart."""
    require(actor, Right.MANAGE_PARTS)
    name = name.strip()
    if not name:
        raise InventoryError("Bitte eine Bezeichnung angeben.")
    part = Part.objects.select_for_update().get(pk=part.pk)
    if part.is_retired():
        raise InventoryError(f"{part} ist ausgesondert; Änderungen sind nicht möglich.")
    if part.name != name:
        part.name = name
        part.save(update_fields=["name"])
    return part


@transaction.atomic
def attach_document(parts: list[Part], document: Document, *, actor: User | None) -> int:
    """Hängt eine GAL an mehrere Teile (Parität 4); die Datei liegt dabei nur einmal vor.

    Gibt die Zahl der Teile zurück, die die GAL neu bekommen haben. Ausgesonderte Teile
    weisen den ganzen Vorgang ab.
    """
    require(actor, Right.MANAGE_PARTS)
    if not parts:
        raise InventoryError("Keine Teile ausgewählt.")
    retired = [str(part) for part in parts if part.is_retired()]
    if retired:
        raise InventoryError(f"Ausgesondert, keine Änderungen möglich: {', '.join(retired)}.")
    added = 0
    for part in parts:
        if not part.documents.filter(pk=document.pk).exists():
            part.documents.add(document)
            added += 1
    return added


@transaction.atomic
def update_component(component: Component, *, actor: User | None, **fields: Any) -> Component:
    """Ändert Felder eines Bauteils (Parität 3).

    Beschreibende Felder ab Trainer/Materialwart; EI, Herstellungsjahr und Datum 1. Einsatz
    nur mit gültiger Sachkunde. „Nächste Prüfung“ ist kein Feld des Bauteils; sie ändert sich
    nur über eine Prüfung.
    """
    unknown = set(fields) - set(COMPONENT_FIELDS)
    if unknown:
        raise InventoryError(f"Nicht änderbare Felder: {', '.join(sorted(unknown))}")
    changed = [name for name, value in fields.items() if getattr(component, name) != value]
    if not changed:
        return component
    if set(changed) & set(PPE_COMPONENT_FIELDS):
        require(actor, Right.PPE_FIELDS)
    if set(changed) - set(PPE_COMPONENT_FIELDS):
        require(actor, Right.MANAGE_PARTS)
    if component.part.is_retired():
        raise InventoryError(f"{component.part} ist ausgesondert; Änderungen sind nicht möglich.")
    for name in changed:
        setattr(component, name, fields[name])
    component.save(update_fields=changed)
    component.refresh_from_db(fields=["ei_key"])
    return component


@transaction.atomic
def record_event(
    part: Part,
    kind: EventKind,
    *,
    actor: User | None,
    comment: str = "",
    at: datetime | None = None,
    inspection_result: "InspectionResult | None" = None,
) -> PartEvent:
    """Erfasst einen Vorgang am Teil und prüft die Fachregeln dafür.

    Die Zeile des Teils wird gesperrt, damit gleichzeitige Vorgänge nacheinander geprüft werden.
    Vorgänge aus einer signierten Prüfung tragen den Verweis auf das Prüfergebnis.
    """
    Part.objects.select_for_update().get(pk=part.pk)
    moment = at or timezone.now()
    require(actor, EVENT_RIGHTS[kind])
    if part.is_retired():
        raise InventoryError(f"{part} ist ausgesondert; weitere Vorgänge sind nicht möglich.")
    if kind == EventKind.RELEASED and actor is not None and inspection_result is None:
        raise InventoryError("Freigeben geht nur über eine PSA-Prüfung.")
    if kind in EVENTS_WITH_COMMENT and not comment.strip():
        raise InventoryError(f"„{kind.label}“ braucht einen Kommentar.")
    if kind == EventKind.LOCKED and part.is_locked():
        raise InventoryError(f"{part} ist bereits gesperrt.")
    if kind == EventKind.UNLOCKED and not part.is_locked():
        raise InventoryError(f"{part} ist nicht gesperrt.")
    if kind == EventKind.RELEASED and part.events.filter(kind=EventKind.RELEASED).exists():
        raise InventoryError(f"{part} ist bereits freigegeben.")
    return PartEvent.objects.create(
        part=part,
        kind=kind,
        actor=actor,
        comment=comment,
        at=moment,
        inspection_result=inspection_result,
    )


def search_parts(parts: QuerySet[Part], query: str) -> QuerySet[Part]:
    """Suche über Bezeichnung, EI (ohne Leerzeichen/Großschreibung), Hersteller, Seriennummer."""
    if not query:
        return parts
    match = (
        Q(name__icontains=query)
        | Q(components__designation__icontains=query)
        | Q(components__model_name__icontains=query)
        | Q(components__manufacturer__icontains=query)
        | Q(components__serial_number__icontains=query)
    )
    key = normalize_ei(query)
    if key:
        match |= Q(components__ei_key__contains=key)
    return parts.filter(match).distinct()
