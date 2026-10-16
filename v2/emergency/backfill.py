"""Nacherfassung nach dem Notbetrieb (Parität 22, Konzept Abschnitt 7).

1. Formularnummer eingeben und Scan hochladen: eine PDF oder Fotos (JPEG/PNG), die mit Typst
   zu einer PDF zusammengefasst werden. Gespeichert wird die PDF unter ihrer SHA-256.
2. Die Daten mit dem Originaldatum vom Formular eintragen. Der Vorgang trägt den Vermerk
   „nachgetragen von X am Y“ und die Formularnummer.
3. Was sich nicht sauber übernehmen lässt, landet auf der Klärungsliste für PSA-Prüfer oder
   Admin, ebenso fehlende Formularnummern (Lücken zwischen erfassten Nummern einer Mappe).

Dieser Teil erfasst Sperrungen. Ausgaben und Rückgaben folgen; Prüfprotokolle in Phase 3 (#80).
"""

import hashlib
import json
import re
import tempfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

import typst
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from accounts.models import User
from accounts.rights import Right, require
from emergency.models import Binder, Clarification, FormKind, FormSheet, LockEntry, PaperForm
from inventory.models import Component, EventKind, Part, normalize_ei
from inventory.services import record_event

NUMBER = re.compile(r"^\s*([A-Za-z])\s*-?\s*0*(\d{1,6})\s*$")
PDF_MAGIC = b"%PDF-"
IMAGE_MAGIC = {b"\xff\xd8\xff": "jpg", b"\x89PNG\r\n\x1a\n": "png"}
MAX_SCAN_FILES = 10
MAX_SCAN_BYTES = 25 * 1024 * 1024
MAX_LOCK_ROWS = 10  # Zeilen auf dem Formular „Sperrung“
# Uhrzeit für Vorgänge, von denen das Formular nur den Tag kennt.
NOON = time(12, 0)


class BackfillError(ValueError):
    """Die Nacherfassung verstößt gegen eine Regel. Die Meldung ist für die Oberfläche."""


Scan = tuple[bytes, str]


# --- Formularnummer und Scan ---


def find_sheet(number: str, kind: FormKind) -> FormSheet:
    """Das gedruckte Formular zur Nummer; weist unbekannte, falsche und erfasste Nummern ab."""
    match = NUMBER.match(number)
    if match is None:
        raise BackfillError(f"„{number}“ ist keine Formularnummer (z. B. {kind.value}-00012).")
    prefix, digits = match.group(1).upper(), int(match.group(2))
    if prefix == FormKind.INSPECTION and kind != FormKind.INSPECTION:
        raise BackfillError("Prüfprotokolle werden ab Phase 3 nacherfasst (#80).")
    if prefix != kind.value:
        raise BackfillError(
            f"{prefix}-{digits:05d} ist keine Formularnummer für „{kind.label}“ "
            f"(sie beginnen mit {kind.value})."
        )
    sheet = FormSheet.objects.filter(kind=kind, number=digits).first()
    if sheet is None:
        raise BackfillError(f"Die Formularnummer {kind.value}-{digits:05d} wurde nicht vergeben.")
    if PaperForm.objects.filter(sheet=sheet).exists():
        raise BackfillError(f"Formular {sheet.label} ist bereits erfasst.")
    return sheet


def _image_kind(content: bytes) -> str | None:
    for magic, suffix in IMAGE_MAGIC.items():
        if content.startswith(magic):
            return suffix
    return None


def scan_to_pdf(scans: Sequence[Scan]) -> bytes:
    """Eine PDF unverändert, oder Fotos als je eine Seite einer neuen PDF."""
    if not scans:
        raise BackfillError("Bitte den Scan des Formulars hochladen (PDF oder Foto).")
    if len(scans) > MAX_SCAN_FILES or sum(len(content) for content, _ in scans) > MAX_SCAN_BYTES:
        raise BackfillError(
            f"Höchstens {MAX_SCAN_FILES} Dateien mit zusammen {MAX_SCAN_BYTES // 2**20} MB."
        )
    pdfs = [content for content, _ in scans if content.startswith(PDF_MAGIC)]
    if pdfs:
        if len(scans) > 1:
            raise BackfillError("Bitte entweder eine PDF oder Fotos hochladen, nicht beides.")
        return pdfs[0]
    kinds = [_image_kind(content) for content, _ in scans]
    if None in kinds:
        bad = next(name for (_, name), kind in zip(scans, kinds, strict=True) if kind is None)
        raise BackfillError(f"{bad} ist kein PDF oder Foto (JPEG, PNG).")
    with tempfile.TemporaryDirectory(prefix="psagent-scan-") as folder:
        names = []
        for index, ((content, _), suffix) in enumerate(zip(scans, kinds, strict=True), 1):
            name = f"seite-{index}.{suffix}"
            (Path(folder) / name).write_bytes(content)
            names.append(name)
        source = Path(folder) / "scan.typ"
        source.write_text(
            "#let seiten = json(bytes(sys.inputs.seiten))\n"
            '#set page(paper: "a4", margin: 0.5cm)\n'
            "#for (i, name) in seiten.enumerate() {\n"
            "  if i > 0 { pagebreak() }\n"
            '  align(center + horizon, image(name, width: 100%, height: 100%, fit: "contain"))\n'
            "}\n",
            encoding="utf-8",
        )
        try:
            result = typst.compile(
                source, root=Path(folder), format="pdf", sys_inputs={"seiten": json.dumps(names)}
            )
        except RuntimeError as error:
            raise BackfillError("Ein Foto ließ sich nicht lesen; bitte neu aufnehmen.") from error
    assert isinstance(result, bytes)  # noqa: S101 – ein Dokument, eine Datei
    return result


def scan_path_for(digest: str) -> Path:
    return Path(settings.MEDIA_ROOT) / "scans" / f"{digest}.pdf"


def scan_path(form: PaperForm) -> Path:
    return scan_path_for(form.scan_sha256)


def _store_scan(pdf: bytes) -> str:
    digest = hashlib.sha256(pdf).hexdigest()
    path = scan_path_for(digest)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(pdf)
    return digest


def _record_form(sheet: FormSheet, scans: Sequence[Scan], actor: User | None) -> PaperForm:
    digest = _store_scan(scan_to_pdf(scans))
    return PaperForm.objects.create(sheet=sheet, scan_sha256=digest, recorded_by=actor)


def _backfill_note(form: PaperForm, actor: User | None) -> str:
    who = (actor.get_full_name() or actor.get_username()) if actor else "Verwaltungsbefehl"
    return (
        f"nachgetragen von {who} am {timezone.localdate():%d.%m.%Y} (Formular {form.sheet.label})"
    )


def _moment(day: date) -> datetime:
    return timezone.make_aware(datetime.combine(day, NOON))


def find_part(ei: str) -> Part | None:
    component = Component.objects.select_related("part").filter(ei_key=normalize_ei(ei)).first()
    return component.part if component else None


# --- Sperrungen ---


@transaction.atomic
def capture_locks(
    number: str, scans: Sequence[Scan], rows: Sequence[dict[str, Any]], *, actor: User | None
) -> PaperForm:
    """Erfasst ein Sperrformular. Jede Zeile wird zur Sperre mit dem Datum vom Formular.

    Ist das Teil schon gesperrt, bleibt die Zeile mit Hinweis erhalten und kommt auf die
    Klärungsliste. Unbekannte EI oder ausgesonderte Teile weisen das ganze Formular ab.
    """
    require(actor, Right.LOCK)
    sheet = find_sheet(number, FormKind.LOCK)
    if not rows:
        raise BackfillError("Bitte mindestens eine Zeile des Formulars eintragen.")
    parts = []
    for row in rows:
        part = find_part(row["ei"])
        if part is None:
            raise BackfillError(f"Kein Teil mit der EI „{row['ei'].strip()}“.")
        if part.is_retired():
            raise BackfillError(f"{part} ist ausgesondert und lässt sich nicht mehr sperren.")
        if not str(row["reason"]).strip() or not str(row["locked_by_name"]).strip():
            raise BackfillError("Jede Sperrung braucht Grund und Namen der sperrenden Person.")
        parts.append(part)
    form = _record_form(sheet, scans, actor)
    note = _backfill_note(form, actor)
    for part, row in zip(parts, rows, strict=True):
        entry: dict[str, Any] = {
            "paper_form": form,
            "part": part,
            "reason": row["reason"].strip(),
            "locked_on": row["locked_on"],
            "locked_by_name": row["locked_by_name"].strip(),
        }
        part.refresh_from_db()
        if part.is_locked():
            entry["note"] = "Teil war bei der Nacherfassung bereits gesperrt; nicht übernommen."
        else:
            entry["part_event"] = record_event(
                part,
                EventKind.LOCKED,
                actor=actor,
                comment=(f"{entry['reason']} – gesperrt von {entry['locked_by_name']}; {note}"),
                at=_moment(row["locked_on"]),
            )
        LockEntry.objects.create(**entry)
    return form


# --- Klärungsliste ---


@dataclass(frozen=True)
class Case:
    """Ein offener Fall der Klärungsliste."""

    key: str
    title: str
    detail: str = ""
    form: PaperForm | None = None


def _gaps() -> Iterable[Case]:
    captured = FormSheet.objects.filter(paper_form__isnull=False)
    binders = Binder.objects.filter(sheets__in=captured).distinct()
    for binder in binders.order_by("created_at", "id"):
        for kind in FormKind:
            sheets = list(binder.sheets.filter(kind=kind).order_by("number"))
            numbers = [sheet.number for sheet in sheets if hasattr(sheet, "paper_form")]
            if not numbers:
                continue
            for sheet in sheets:
                if min(numbers) < sheet.number < max(numbers) and sheet.number not in numbers:
                    yield Case(
                        key=f"luecke:{sheet.label}",
                        title=f"Formular {sheet.label} fehlt",
                        detail=(
                            f"Aus der {binder}; Formulare davor und danach sind erfasst. "
                            "Blatt suchen und nacherfassen oder klären."
                        ),
                    )


def _unapplied_locks() -> Iterable[Case]:
    entries = LockEntry.objects.filter(part_event__isnull=True).select_related(
        "part", "paper_form__sheet"
    )
    for entry in entries.order_by("id"):
        yield Case(
            key=f"sperre:{entry.pk}",
            title=f"Sperrung von {entry.part} aus Formular {entry.paper_form.sheet.label}",
            detail=f"{entry.note} Grund laut Formular: {entry.reason}",
            form=entry.paper_form,
        )


def open_cases() -> list[Case]:
    resolved = set(Clarification.objects.values_list("key", flat=True))
    return [case for case in (*_gaps(), *_unapplied_locks()) if case.key not in resolved]


@transaction.atomic
def resolve(key: str, *, note: str, actor: User | None) -> Clarification:
    """Klärt einen offenen Fall mit Begründung; die Klärung wird nur angehängt."""
    require(actor, Right.CLARIFY)
    if not note.strip():
        raise BackfillError("Bitte eine Begründung angeben.")
    if key not in {case.key for case in open_cases()}:
        raise BackfillError("Das ist kein offener Fall der Klärungsliste.")
    return Clarification.objects.create(key=key, note=note.strip(), resolved_by=actor)
