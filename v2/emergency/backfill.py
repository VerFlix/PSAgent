"""Nacherfassung nach dem Notbetrieb (Parität 22, Konzept Abschnitt 7).

1. Formularnummer eingeben und Scan hochladen: eine PDF oder Fotos (JPEG/PNG), die mit Typst
   zu einer PDF zusammengefasst werden. Gespeichert wird die PDF unter ihrer SHA-256.
2. Die Daten mit dem Originaldatum vom Formular eintragen. Der Vorgang trägt den Vermerk
   „nachgetragen von X am Y“ und die Formularnummer.
3. Was sich nicht sauber übernehmen lässt, landet auf der Klärungsliste für PSA-Prüfer oder
   Admin, ebenso fehlende Formularnummern (Lücken zwischen erfassten Nummern einer Mappe).

Erfasst werden Sperrungen sowie Ausgaben und Rückgaben; Prüfprotokolle folgen in Phase 3 (#80).
Was auf Papier ausgegeben wurde, wird eingetragen, auch wenn es gegen eine Regel verstieß (das
Teil ist ja draußen); ein PSA-Prüfer sieht den Fall auf der Klärungsliste. Nur Doppelbuchungen
verhindert die Datenbank: Sie warten, bis ein PSA-Prüfer die Reservierung storniert und die
Ausleihe übernimmt oder sie mit Begründung verwirft.
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
from django.db.models import Q
from django.utils import timezone

from accounts.models import User
from accounts.rights import Right, has_right, require
from emergency.models import (
    Binder,
    Clarification,
    FormKind,
    FormSheet,
    LoanForm,
    LoanFormLoan,
    LoanFormPart,
    LoanFormResult,
    LockEntry,
    PaperForm,
)
from inspections.services import next_inspection_due
from inventory.models import Component, EventKind, Part, normalize_ei
from inventory.services import record_event
from lending import services as lending
from lending.models import (
    ACTIVE_STATUSES,
    Booking,
    Borrower,
    BorrowerContact,
    Loan,
    LoanEvent,
    LoanEventKind,
    LoanStatus,
    Signature,
    SignatureOrigin,
)

NUMBER = re.compile(r"^\s*([A-Za-z])\s*-?\s*0*(\d{1,6})\s*$")
PDF_MAGIC = b"%PDF-"
IMAGE_MAGIC = {b"\xff\xd8\xff": "jpg", b"\x89PNG\r\n\x1a\n": "png"}
MAX_SCAN_FILES = 10
MAX_SCAN_BYTES = 25 * 1024 * 1024
MAX_LOCK_ROWS = 10  # Zeilen auf dem Formular „Sperrung“
# Obergrenze je Foto, gelesen aus dem Dateikopf vor dem Entpacken (Schutz vor Pixelbomben).
# Handykameras liefern meist 12–50 Megapixel.
MAX_PIXELS = 60_000_000
# Obergrenze für alle Fotos eines Formulars zusammen (z. B. 10 Fotos mit 12 Megapixeln).
MAX_TOTAL_PIXELS = 150_000_000
# JPEG-Marker mit Bildgröße (SOF0–SOF15 ohne DHT, JPG und DAC).
_JPEG_SOF = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
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


def _image_size(content: bytes, kind: str) -> tuple[int, int] | None:
    """Breite und Höhe aus dem Dateikopf, ohne das Bild zu entpacken; None, wenn unlesbar."""
    if kind == "png":
        if len(content) < 24 or content[12:16] != b"IHDR":
            return None
        return int.from_bytes(content[16:20]), int.from_bytes(content[20:24])
    index = 2
    while index + 4 <= len(content):
        if content[index] != 0xFF:
            return None
        marker = content[index + 1]
        index += 2
        if marker == 0xFF:
            index -= 1  # Füllbyte
            continue
        if marker == 0x01 or 0xD0 <= marker <= 0xD9:
            continue
        length = int.from_bytes(content[index : index + 2])
        if marker in _JPEG_SOF:
            if index + 7 > len(content):
                return None
            height = int.from_bytes(content[index + 3 : index + 5])
            width = int.from_bytes(content[index + 5 : index + 7])
            return width, height
        if length < 2:
            return None
        index += length
    return None


def _check_image(content: bytes, kind: str, name: str) -> int:
    """Prüft ein Foto anhand des Dateikopfs und gibt seine Bildpunkte zurück."""
    size = _image_size(content, kind)
    if size is None or 0 in size:
        raise BackfillError(f"{name} ließ sich nicht lesen; bitte neu aufnehmen.")
    width, height = size
    if width * height > MAX_PIXELS:
        raise BackfillError(
            f"{name} ist mit {width} x {height} Bildpunkten zu groß "
            f"(höchstens {MAX_PIXELS // 1_000_000} Megapixel)."
        )
    return width * height


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
    total = 0
    for (content, name), kind in zip(scans, kinds, strict=True):
        assert kind is not None  # noqa: S101 – oben geprüft
        total += _check_image(content, kind, name)
    if total > MAX_TOTAL_PIXELS:
        raise BackfillError(
            f"Die Fotos sind zusammen zu groß (höchstens {MAX_TOTAL_PIXELS // 1_000_000} "
            "Megapixel); bitte mit geringerer Auflösung aufnehmen."
        )
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


def borrowers_of(paper: PaperForm) -> set[int]:
    """Entleiher auf einem Formular: aus der Ausgabe und aus den zurückgenommenen Ausleihen."""
    form = getattr(paper, "loan_form", None)
    if form is None:
        return set()
    linked = LoanFormLoan.objects.filter(loan_form=form).values_list(
        "loan__booking__borrower_id", flat=True
    )
    return {*linked, *([form.borrower_id] if form.borrower_id else [])}


def erase_scans(borrower: Borrower, *, actor: User | None) -> None:
    """Löscht die Scans der Ausleihformulare eines Entleihers; die Prüfsummen bleiben (#63).

    Ein Scan kann mehrere Entleiher zeigen (Rückgabe für mehrere), und eine Datei mit gleichem
    Inhalt kann zu mehreren Formularen gehören. Die Datei geht erst, wenn alle Entleiher darauf
    gelöscht sind. Gelöscht wird nach dem Commit, damit ein abgebrochener Vorgang keine Scans
    verliert.
    """
    require(actor, Right.MANAGE_USERS)
    forms = PaperForm.objects.filter(
        Q(loan_form__borrower=borrower) | Q(loan_form__loans__loan__booking__borrower=borrower)
    )
    digests = set(forms.values_list("scan_sha256", flat=True))
    papers = PaperForm.objects.filter(scan_sha256__in=digests).select_related("loan_form")
    on_forms = [(paper.scan_sha256, borrowers_of(paper)) for paper in papers]
    others = set().union(*(ids for _, ids in on_forms)) - {borrower.pk}
    remaining = set(
        BorrowerContact.objects.filter(borrower_id__in=others).values_list("borrower_id", flat=True)
    )
    # Ein Formular ohne Entleiher (Sperrformular) oder mit noch vorhandenen Entleihern behält
    # die Datei.
    keep = {digest for digest, ids in on_forms if not ids or ids & remaining}
    paths = [scan_path_for(digest) for digest in sorted(digests - keep)]

    def delete() -> None:
        for path in paths:
            path.unlink(missing_ok=True)

    transaction.on_commit(delete)


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
    """Mittag des Tages vom Formular; für heute höchstens jetzt, damit der Vorgang schon gilt."""
    return min(timezone.make_aware(datetime.combine(day, NOON)), timezone.now())


# Vorgänge, deren Reihenfolge über Sperr- und Prüfstatus entscheidet.
STATUS_EVENTS = (EventKind.LOCKED, EventKind.UNLOCKED, EventKind.RETIRED)


def _check_form_date(day: date, sheet: FormSheet) -> None:
    """Das Datum vom Formular liegt zwischen dem Druck der Mappe und heute."""
    printed = timezone.localtime(sheet.binder.created_at).date()
    if day > timezone.localdate():
        raise BackfillError(f"Das Datum {day:%d.%m.%Y} liegt in der Zukunft.")
    if day < printed:
        raise BackfillError(
            f"Das Datum {day:%d.%m.%Y} liegt vor dem Druck der Notfallmappe "
            f"({printed:%d.%m.%Y}); das Formular kann nicht älter sein."
        )


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
        _check_form_date(row["locked_on"], sheet)
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
        moment = _moment(row["locked_on"])
        if part.is_locked():
            entry["note"] = "Teil war bei der Nacherfassung bereits gesperrt; nicht übernommen."
        elif part.events.filter(kind__in=STATUS_EVENTS, at__gte=moment).exists():
            entry["note"] = (
                "Am Teil gibt es spätere Sperr- oder Prüfvorgänge; nicht übernommen, damit die "
                "Reihenfolge stimmt."
            )
        else:
            entry["part_event"] = record_event(
                part,
                EventKind.LOCKED,
                actor=actor,
                comment=(f"{entry['reason']} – gesperrt von {entry['locked_by_name']}; {note}"),
                at=moment,
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
    loan_form: LoanForm | None = None
    can_apply: bool = False


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


def _loan_form_cases() -> Iterable[Case]:
    forms = LoanForm.objects.select_related("paper_form__sheet").prefetch_related("parts__part")
    for form in forms.order_by("id"):
        label = form.paper_form.sheet.label
        if not hasattr(form, "result"):
            conflicts = _conflicts(form)
            running = [loan for loan in conflicts if loan.status == LoanStatus.LENT]
            lines = [
                f"{loan.part} ist {loan.start:%d.%m.%Y}–{loan.end:%d.%m.%Y} "
                f"{loan.get_status_display()}."
                for loan in conflicts
            ]
            if running:
                lines.append("Zuerst die laufende Ausleihe klären (z. B. Rückgabe nacherfassen).")
            yield Case(
                key=f"ausgabe:{form.pk}",
                title=f"Doppelbuchung: Ausgabe aus Formular {label} nicht eingetragen",
                detail=" ".join(lines + form.warnings.splitlines()),
                form=form.paper_form,
                loan_form=form,
                can_apply=bool(conflicts) and not running,
            )
        elif form.warnings:
            yield Case(
                key=f"ausgabe-pruefen:{form.pk}",
                title=f"Ausgabe/Rückgabe aus Formular {label} prüfen",
                detail=" ".join(form.warnings.splitlines()),
                form=form.paper_form,
                loan_form=form,
            )


def open_cases() -> list[Case]:
    resolved = set(Clarification.objects.values_list("key", flat=True))
    cases = (*_gaps(), *_unapplied_locks(), *_loan_form_cases())
    return [case for case in cases if case.key not in resolved]


@transaction.atomic
def resolve(key: str, *, note: str, actor: User | None) -> Clarification:
    """Klärt einen offenen Fall mit Begründung; die Klärung wird nur angehängt."""
    require(actor, Right.CLARIFY)
    if not note.strip():
        raise BackfillError("Bitte eine Begründung angeben.")
    if key not in {case.key for case in open_cases()}:
        raise BackfillError("Das ist kein offener Fall der Klärungsliste.")
    return Clarification.objects.create(key=key, note=note.strip(), resolved_by=actor)


# --- Ausgaben und Rückgaben ---

MAX_LOAN_PARTS = 6  # Zeilen auf dem Formular „Ausgabe und Rückgabe“


def _parts_of(eis: Sequence[str]) -> list[Part]:
    cleaned = [value.strip() for value in eis if value.strip()]
    if not cleaned:
        raise BackfillError("Bitte mindestens ein Teil (EI) vom Formular eintragen.")
    if len(cleaned) > MAX_LOAN_PARTS:
        raise BackfillError(f"Das Formular hat höchstens {MAX_LOAN_PARTS} Zeilen für Teile.")
    parts: list[Part] = []
    for value in cleaned:
        part = find_part(value)
        if part is None:
            raise BackfillError(f"Kein Teil mit der EI „{value}“.")
        if part.is_retired():
            raise BackfillError(f"{part} ist ausgesondert.")
        if part in parts:
            raise BackfillError(f"{part} steht zweimal auf dem Formular.")
        parts.append(part)
    return parts


def _validate(data: dict[str, Any], sheet: FormSheet) -> None:
    issued, returned = data.get("issued_on"), data.get("returned_on")
    if issued is None and returned is None:
        raise BackfillError("Bitte Ausgabe oder Rückgabe vom Formular eintragen.")
    if issued is not None:
        _check_form_date(issued, sheet)
        planned = data.get("planned_return")
        if planned is None:
            raise BackfillError("Bitte „Rückgabe geplant am“ vom Formular eintragen.")
        if planned < issued:
            raise BackfillError("„Rückgabe geplant am“ liegt vor der Ausgabe.")
        if not str(data.get("borrower_name", "")).strip():
            raise BackfillError("Bitte den Namen des Entleihers vom Formular eintragen.")
        if not str(data.get("issued_by_name", "")).strip():
            raise BackfillError("Bitte den Namen der ausgebenden Person eintragen.")
    if returned is not None:
        _check_form_date(returned, sheet)
        if issued is not None and returned < issued:
            raise BackfillError("Die Rückgabe liegt vor der Ausgabe.")
        if not str(data.get("received_by_name", "")).strip():
            raise BackfillError("Bitte den Namen der rücknehmenden Person eintragen.")


def _borrower(data: dict[str, Any], actor: User | None) -> Borrower:
    """Bekannter Entleiher bei eindeutigem Namen, sonst neu; Angaben vom Papier gelten.

    Einen bekannten Entleiher verknüpft nur, wer Kontaktdaten ohnehin sehen darf. Sonst könnte
    ein Verleiher über eine erfundene Ausgabe mit fremdem Namen eine „laufende Ausleihe“ und
    damit Einblick in dessen Kontaktdaten erzeugen, oder sie überschreiben.
    """
    name = str(data["borrower_name"]).strip()
    address, email, phone = (str(data.get(key) or "") for key in ("address", "email", "phone"))
    may_link = actor is None or has_right(actor, Right.VIEW_CONTACTS)
    matches = list(BorrowerContact.objects.filter(name__iexact=name)[:2]) if may_link else []
    if len(matches) == 1:
        borrower = matches[0].borrower
        lending.overwrite_contact(borrower, actor=actor, address=address, email=email, phone=phone)
        return borrower
    return lending.create_borrower(name, address=address, email=email, phone=phone, actor=actor)


def _checkout_warnings(form: LoanForm, parts: Sequence[Part]) -> list[str]:
    """Was laut Stand gegen die Regeln der Ausgabe verstieß; das Teil ist trotzdem draußen."""
    assert form.issued_on is not None and form.planned_return is not None  # noqa: S101
    warnings = []
    if not form.quick_check:
        warnings.append("Kurzkontrolle nicht angekreuzt.")
    if not form.gal_provided:
        warnings.append("„GAL bereitgestellt“ nicht angekreuzt.")
    contact = BorrowerContact.objects.filter(borrower=form.borrower).first()
    if contact is None or not contact.address.strip():
        warnings.append("Adresse des Entleihers fehlt.")
    if contact is None or not (contact.email.strip() or contact.phone.strip()):
        warnings.append("E-Mail oder Telefon des Entleihers fehlt.")
    moment = _moment(form.issued_on)
    for part in parts:
        if part.is_locked(moment):
            warnings.append(f"{part} war bei der Ausgabe gesperrt.")
        if not part.is_released(moment):
            warnings.append(f"{part} war noch nicht freigegeben.")
        elif not part.has_no_ppe(moment):
            due = next_inspection_due(part)
            if due is None:
                warnings.append(f"{part} hatte keine gültige PSA-Prüfung.")
            elif due < form.issued_on:
                warnings.append(f"{part} war seit {due:%d.%m.%Y} zur PSA-Prüfung fällig.")
            elif form.planned_return > due:
                warnings.append(f"{part} sollte nach der PSA-Prüfung am {due:%d.%m.%Y} zurück.")
        if not part.documents.exists():
            warnings.append(f"Für {part} ist keine GAL hinterlegt.")
    return warnings


def _conflicts(form: LoanForm) -> list[Loan]:
    """Reservierungen und Ausleihen, die sich mit der Ausgabe auf dem Papier überschneiden."""
    if form.issued_on is None or form.planned_return is None:
        return []
    period = lending._period(form.issued_on, form.planned_return)
    parts = [entry.part_id for entry in form.parts.all()]
    return list(
        Loan.objects.select_related("part")
        .filter(part_id__in=parts, status__in=ACTIVE_STATUSES, period__overlap=period)
        .order_by("part_id", "id")
    )


def _paper_signature(name: str) -> Signature:
    return Signature.record([], name, SignatureOrigin.PAPER)


def _return(loan: Loan, form: LoanForm, note: str, actor: User | None) -> None:
    assert form.returned_on is not None  # noqa: S101 – nur mit Rückgabe aufgerufen
    changed = Loan.objects.filter(pk=loan.pk, status=LoanStatus.LENT).update(
        status=LoanStatus.RETURNED
    )
    if changed != 1:
        raise BackfillError(f"{loan} ist nicht mehr ausgeliehen.")
    comment = "; ".join(filter(None, [form.return_comment, note]))
    LoanEvent.objects.create(
        loan=loan,
        kind=LoanEventKind.RETURNED,
        at=_moment(form.returned_on),
        actor=actor,
        actor_name=form.received_by_name,
        quick_check=form.return_quick_check,
        comment=comment,
        signature=_paper_signature(loan.booking.borrower.display_name),
    )


def _running_loan(part: Part, returned_on: date) -> Loan | None:
    """Laufende Ausleihe von vor dem Ausfall, die mit der Rückgabe auf Papier endet."""
    return (
        Loan.objects.select_related("booking__borrower")
        .filter(part=part, status=LoanStatus.LENT, period__startswith__lte=returned_on)
        .first()
    )


def _lock_hint(part: Part, form: LoanForm) -> str | None:
    """Warum die Sperre vom Formular nicht eingetragen wird; None, wenn sie es wird."""
    assert form.returned_on is not None  # noqa: S101
    if part.is_locked():
        return f"{part} war bereits gesperrt; Sperre vom Formular nicht eingetragen."
    if part.events.filter(kind__in=STATUS_EVENTS, at__gte=_moment(form.returned_on)).exists():
        return f"{part} hat spätere Sperr- oder Prüfvorgänge; Sperre nicht eingetragen."
    return None


def _hints(form: LoanForm, parts: Sequence[Part]) -> list[str]:
    """Hinweise, die schon vor dem Eintragen feststehen (Rückgabe ohne Ausleihe, Sperre)."""
    hints = []
    returned = list(parts)
    if form.issued_on is None and form.returned_on is not None:
        returned = []
        for part in parts:
            if _running_loan(part, form.returned_on) is None:
                hints.append(
                    f"Für {part} gibt es keine laufende Ausleihe; Rückgabe nicht eingetragen."
                )
            else:
                returned.append(part)
    if form.returned_on is not None and form.lock_reason:
        hints.extend(hint for part in returned if (hint := _lock_hint(part, form)))
    return hints


def _apply(form: LoanForm, actor: User | None, *, applied_by: User | None) -> LoanFormResult:
    """Trägt das Formular in den Verleih ein: Ausgabe, Rückgabe und Sperre vom Papier."""
    note = _backfill_note(form.paper_form, actor)
    parts = [entry.part for entry in form.parts.select_related("part")]
    booking = None
    loans: list[Loan] = []
    if form.issued_on is not None:
        assert form.borrower is not None and form.planned_return is not None  # noqa: S101
        booking = Booking.objects.create(borrower=form.borrower, created_by=actor)
        period = lending._period(form.issued_on, form.planned_return)
        signature = _paper_signature(form.borrower.display_name)
        for part in parts:
            loan = Loan.objects.create(
                booking=booking, part=part, period=period, status=LoanStatus.LENT
            )
            LoanEvent.objects.create(
                loan=loan,
                kind=LoanEventKind.CHECKED_OUT,
                at=_moment(form.issued_on),
                actor=actor,
                actor_name=form.issued_by_name,
                quick_check=form.quick_check,
                gal_provided=form.gal_provided,
                comment=note,
                signature=signature,
            )
            loans.append(loan)
    elif form.returned_on is not None:
        running = [_running_loan(part, form.returned_on) for part in parts]
        loans = [loan for loan in running if loan is not None]
    if form.returned_on is not None:
        for loan in loans:
            locks = form.lock_reason and _lock_hint(loan.part, form) is None
            _return(loan, form, note, actor)
            if locks:
                record_event(
                    loan.part,
                    EventKind.LOCKED,
                    actor=actor,
                    comment=f"{form.lock_reason} – gesperrt bei der Rückgabe; {note}",
                    at=_moment(form.returned_on),
                )
    LoanFormLoan.objects.bulk_create(LoanFormLoan(loan_form=form, loan=loan) for loan in loans)
    return LoanFormResult.objects.create(loan_form=form, booking=booking, applied_by=applied_by)


def _overlaps(parts: Sequence[Part], form: LoanForm) -> bool:
    assert form.issued_on is not None and form.planned_return is not None  # noqa: S101
    period = lending._period(form.issued_on, form.planned_return)
    return Loan.objects.filter(
        part__in=parts, status__in=ACTIVE_STATUSES, period__overlap=period
    ).exists()


@transaction.atomic
def capture_loan_form(
    number: str, scans: Sequence[Scan], data: dict[str, Any], *, actor: User | None
) -> PaperForm:
    """Erfasst ein Formular „Ausgabe und Rückgabe“ mit Scan und Datum vom Papier.

    Bei einer Doppelbuchung bleibt das Formular mit Scan erfasst; die Ausleihe wartet auf die
    Klärungsliste (`apply_loan_form`). Regelverstöße stehen als Hinweise am Formular.
    """
    require(actor, Right.LEND)
    if str(data.get("lock_reason") or "").strip():
        require(actor, Right.LOCK)
    sheet = find_sheet(number, FormKind.LOAN)
    parts = _parts_of(data.get("eis", []))
    _validate(data, sheet)
    paper = _record_form(sheet, scans, actor)
    issued = data.get("issued_on")
    form = LoanForm(
        paper_form=paper,
        borrower=_borrower(data, actor) if issued is not None else None,
        quick_check=bool(data.get("quick_check")),
        gal_provided=bool(data.get("gal_provided")),
        issued_on=issued,
        planned_return=data.get("planned_return") if issued is not None else None,
        issued_by_name=str(data.get("issued_by_name") or "").strip(),
        returned_on=data.get("returned_on"),
        return_quick_check=bool(data.get("return_quick_check")),
        return_comment=str(data.get("return_comment") or "").strip(),
        received_by_name=str(data.get("received_by_name") or "").strip(),
        lock_reason=str(data.get("lock_reason") or "").strip(),
    )
    warnings = _checkout_warnings(form, parts) if issued is not None else []
    form.warnings = "\n".join([*warnings, *_hints(form, parts)])
    form.save()
    LoanFormPart.objects.bulk_create(LoanFormPart(loan_form=form, part=part) for part in parts)
    if issued is None or not _overlaps(parts, form):
        _apply(form, actor, applied_by=actor)
    return paper


@transaction.atomic
def apply_loan_form(form: LoanForm, *, note: str, actor: User | None) -> LoanFormResult:
    """Doppelbuchung klären: überschneidende Reservierungen stornieren, Ausgabe eintragen."""
    require(actor, Right.CLARIFY)
    if not note.strip():
        raise BackfillError("Bitte eine Begründung angeben.")
    if LoanFormResult.objects.filter(loan_form=form).exists():
        raise BackfillError("Dieses Formular ist bereits eingetragen.")
    conflicts = _conflicts(form)
    running = [loan for loan in conflicts if loan.status == LoanStatus.LENT]
    if running:
        raise BackfillError(
            f"{running[0].part} ist im Zeitraum als laufende Ausleihe erfasst; zuerst diese klären."
        )
    label = form.paper_form.sheet.label
    if conflicts:
        lending.cancel(
            conflicts, actor=actor, comment=f"storniert für die Ausgabe aus Formular {label}"
        )
    result = _apply(form, actor, applied_by=actor)
    Clarification.objects.create(key=f"ausgabe:{form.pk}", note=note.strip(), resolved_by=actor)
    return result
