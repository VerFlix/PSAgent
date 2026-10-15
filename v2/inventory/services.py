"""Anlegen von Teilen, Speichern von GAL-Dokumenten und Erfassen von Vorgängen.

Die Fachregeln, die die Datenbank nicht allein prüfen kann, stehen hier: Ein Produkt hat genau
ein Bauteil, ein System mindestens eines; nach dem Aussondern gibt es keine Vorgänge mehr;
eine Sperre setzt ein nicht gesperrtes Teil voraus.

Was die PSA betrifft, braucht eine gültige Sachkunde (CLAUDE.md, Fachregeln): Entsperren,
Aussondern, „keine PSA“ setzen oder aufheben und die PSA-relevanten Felder EI, Herstellungsjahr
und Datum 1. Einsatz ändern. Freigeben geht nur über eine PSA-Prüfung. Sperren darf jede Rolle;
die übrigen Rollenregeln prüft #12.
"""

import hashlib
from datetime import datetime
from typing import TYPE_CHECKING, Any

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.validators import URLValidator
from django.db import transaction
from django.utils import timezone

from accounts.models import User
from accounts.permissions import require_inspection_right
from inventory.models import (
    EVENTS_WITH_COMMENT,
    Component,
    Document,
    EventKind,
    Part,
    PartEvent,
    PartKind,
)

if TYPE_CHECKING:
    from inspections.models import InspectionResult

PDF_MAGIC = b"%PDF-"

# Vorgänge, die eine gültige Sachkunde brauchen. Sperren gehört nicht dazu.
EVENTS_NEEDING_QUALIFICATION = (
    EventKind.RELEASED,
    EventKind.UNLOCKED,
    EventKind.RETIRED,
    EventKind.NO_PPE_SET,
    EventKind.NO_PPE_CLEARED,
)

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
    """Speichert eine GAL-Datei. Gleicher Inhalt wird nur einmal gespeichert.

    Angenommen werden nur PDFs (erkannt am Dateianfang, nicht an der Endung); gespeichert wird
    immer als `.pdf`, damit keine hochgeladene Datei als HTML oder Skript ausgeliefert wird.
    `filename` dient nur der Fehlermeldung.
    """
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
    try:
        _validate_url(url)
    except ValidationError as error:
        raise InventoryError(f"Ungültiger Link: {url!r} (nur http und https).") from error
    existing = Document.objects.filter(url=url, file="").first()
    if existing is not None:
        return existing
    return Document.objects.create(title=title, url=url, created_by=actor)


@transaction.atomic
def update_component(component: Component, *, actor: User | None, **fields: Any) -> Component:
    """Ändert Felder eines Bauteils (Parität 3).

    EI, Herstellungsjahr und Datum 1. Einsatz nur mit gültiger Sachkunde. „Nächste Prüfung“
    ist kein Feld des Bauteils; sie ändert sich nur über eine Prüfung.
    """
    unknown = set(fields) - set(COMPONENT_FIELDS)
    if unknown:
        raise InventoryError(f"Nicht änderbare Felder: {', '.join(sorted(unknown))}")
    changed = [name for name, value in fields.items() if getattr(component, name) != value]
    if not changed:
        return component
    if set(changed) & set(PPE_COMPONENT_FIELDS):
        require_inspection_right(actor, "Das Ändern von EI, Herstellungsjahr oder Datum 1. Einsatz")
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
    if kind in EVENTS_NEEDING_QUALIFICATION:
        require_inspection_right(actor, f"„{kind.label}“")
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
