"""Reservieren, ausgeben, zurücknehmen, stornieren; Entleiher anlegen und pflegen.

`unavailability_reasons()` ist die eine Stelle für die Verfügbarkeitsregel (Parität 13): nicht
ausgesondert, nicht gesperrt, freigegeben, PSA-Prüfung aktuell (oder „keine PSA“), Rückgabe
spätestens am Tag der nächsten Prüfung, keine Überschneidung, nicht noch überfällig draußen.
Die Überschneidung prüft zusätzlich die Datenbank.

Rechte (Rechtetabelle, `accounts.rights`): Verleihvorgänge ab Verleiher; Reservierungen anderer
stornieren erst ab Trainer/Materialwart. Kontaktdaten ändern darf, wer verleihen darf und die
Kontaktdaten dieses Entleihers sehen darf; löschen nur ein Admin oder ein Verwaltungsbefehl.
"""

from collections.abc import Sequence
from datetime import date
from typing import Any

from django.db import IntegrityError, transaction
from django.db.backends.postgresql.psycopg_any import DateRange
from django.utils import timezone

from accounts.models import User
from accounts.rights import Right, require
from inspections.services import next_inspection_due
from inventory.models import EventKind, Part
from inventory.services import record_event
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
)

CONTACT_FIELDS = ("name", "address", "email", "phone")


class LendingError(ValueError):
    """Ein Verleihvorgang verstößt gegen eine Fachregel."""


def today() -> date:
    """Heutiges Datum nach deutscher Zeit (in Tests ersetzbar)."""
    return timezone.localdate()


# --- Entleiher ---


@transaction.atomic
def create_borrower(
    name: str,
    *,
    address: str = "",
    email: str = "",
    phone: str = "",
    user: User | None = None,
    actor: User | None = None,
) -> Borrower:
    """Legt einen Entleiher mit Kontaktdaten an. Kontaktdaten dürfen unvollständig sein."""
    require(actor, Right.LEND)
    borrower = Borrower.objects.create(user=user, created_by=actor)
    BorrowerContact.objects.create(
        borrower=borrower, name=name, address=address, email=email, phone=phone
    )
    return borrower


def update_contact(borrower: Borrower, *, actor: User | None, **fields: str) -> BorrowerContact:
    """Ändert oder ergänzt Kontaktdaten (Parität 16)."""
    require(actor, Right.LEND)
    require(actor, Right.VIEW_CONTACTS, borrower)
    unknown = set(fields) - set(CONTACT_FIELDS)
    if unknown:
        raise LendingError(f"Unbekannte Kontaktfelder: {', '.join(sorted(unknown))}")
    contact = BorrowerContact.objects.get(borrower=borrower)
    for key, value in fields.items():
        setattr(contact, key, value)
    contact.updated_at = timezone.now()
    contact.save()
    return contact


def overwrite_contact(borrower: Borrower, *, actor: User | None, **fields: str) -> BorrowerContact:
    """Kontaktdaten bei Reservierung und Ausgabe eingeben oder überschreiben (Parität 14, 16).

    Für Verleiher, die die bisherigen Werte nicht sehen dürfen: Nur ausgefüllte Felder ersetzen
    die gespeicherten, leere bleiben unverändert. Die Daten gelten danach als bestätigt.
    """
    require(actor, Right.LEND)
    unknown = set(fields) - set(CONTACT_FIELDS)
    if unknown:
        raise LendingError(f"Unbekannte Kontaktfelder: {', '.join(sorted(unknown))}")
    given = {key: value.strip() for key, value in fields.items() if value and value.strip()}
    contact = BorrowerContact.objects.select_for_update().get(borrower=borrower)
    if given:
        for key, value in given.items():
            setattr(contact, key, value)
        contact.updated_at = timezone.now()
    contact.confirmed_at = timezone.now()
    contact.save()
    return contact


def confirm_contact(borrower: Borrower, *, actor: User | None) -> BorrowerContact:
    """Nachgefragt: Kontaktdaten unverändert seit `checked_at` (Häkchen bei der Ausgabe)."""
    return overwrite_contact(borrower, actor=actor)


SEARCH_MIN_LENGTH = 3
SEARCH_LIMIT = 10


def search_borrowers(query: str, *, actor: User | None) -> list[Borrower]:
    """Entleiher nach Namen, ab 3 Zeichen, höchstens 10 Treffer (für die Ausgabe).

    Liefert nur Entleiher mit Kontaktdaten; die Oberfläche zeigt Verleihern nur den Namen.
    """
    require(actor, Right.LEND)
    query = query.strip()
    if len(query) < SEARCH_MIN_LENGTH:
        return []
    return list(
        Borrower.objects.filter(contact__name__icontains=query)
        .select_related("contact")
        .order_by("contact__name", "pk")[:SEARCH_LIMIT]
    )


def erase_contact(borrower: Borrower, *, actor: User | None) -> None:
    """Löscht die Kontaktdaten nach Ablauf der Löschfrist; die Ausleihhistorie bleibt."""
    require(actor, Right.MANAGE_USERS)
    BorrowerContact.objects.filter(borrower=borrower).delete()


# --- Verfügbarkeit ---


def _period(start: date, end: date) -> DateRange:
    if end < start:
        raise LendingError("Der Zeitraum endet vor seinem Beginn.")
    return DateRange(start, end, bounds="[]")


def unavailability_reasons(
    part: Part, start: date, end: date, *, ignore: Loan | None = None
) -> list[str]:
    """Gründe, warum `part` im Zeitraum [start, end] nicht verliehen werden kann; leer = frei."""
    reasons: list[str] = []
    if part.is_retired():
        reasons.append("ist ausgesondert")
    elif part.is_locked():
        reasons.append("ist gesperrt")
    if not part.is_released():
        reasons.append("ist neu und noch nicht freigegeben")
    elif not part.has_no_ppe():
        # Wie im Altsystem: Prüfung gilt noch heute, Rückgabe bis einschließlich Prüfungstag.
        due = next_inspection_due(part)
        if due is None:
            reasons.append("hat keine gültige PSA-Prüfung")
        elif due < today():
            reasons.append(f"ist seit {due:%d.%m.%Y} zur PSA-Prüfung fällig")
        elif end > due:
            reasons.append(f"muss vor der nächsten PSA-Prüfung am {due:%d.%m.%Y} zurück sein")

    loans = Loan.objects.filter(part=part)
    if ignore is not None:
        loans = loans.exclude(pk=ignore.pk)
    if loans.filter(status__in=ACTIVE_STATUSES, period__overlap=_period(start, end)).exists():
        reasons.append("ist im Zeitraum bereits vergeben")
    if loans.filter(status=LoanStatus.LENT, period__endswith__lte=today()).exists():
        reasons.append("ist noch nicht zurückgegeben (überfällig)")
    return reasons


def _check_available(items: Sequence[tuple[Part, Loan | None]], start: date, end: date) -> None:
    errors = [
        f"{part} {', '.join(reasons)}"
        for part, loan in items
        if (reasons := unavailability_reasons(part, start, end, ignore=loan))
    ]
    if errors:
        raise LendingError("; ".join(errors) + ".")


def _lock_parts(parts: Sequence[Part]) -> None:
    """Sperrt die Zeilen der Teile, damit gleichzeitige Buchungen nacheinander prüfen."""
    list(Part.objects.select_for_update().filter(pk__in=[p.pk for p in parts]).order_by("pk"))


# --- Ausgabe und Rückgabe: Pflichtangaben ---


# Grenzen der Unterschrift (Striche aus dem Unterschriftsfeld); schützt vor riesigen Anfragen.
SIGNATURE_MAX_STROKES = 100
SIGNATURE_MAX_POINTS = 5000
SIGNATURE_MAX_COORDINATE = 10000


def _point_ok(point: Any) -> bool:
    return (
        isinstance(point, list | tuple)
        and len(point) == 2
        and all(
            isinstance(v, int | float)
            and not isinstance(v, bool)
            and 0 <= v <= SIGNATURE_MAX_COORDINATE
            for v in point
        )
    )


def _signature(strokes: Any, signer_name: str) -> Signature:
    if not signer_name.strip():
        raise LendingError("Name der unterschreibenden Person fehlt.")
    valid = (
        isinstance(strokes, list)
        and 0 < len(strokes) <= SIGNATURE_MAX_STROKES
        and all(isinstance(stroke, list) and stroke for stroke in strokes)
        and sum(len(stroke) for stroke in strokes) <= SIGNATURE_MAX_POINTS
        and all(_point_ok(point) for stroke in strokes for point in stroke)
    )
    if not valid:
        raise LendingError("Unterschrift fehlt oder ist ungültig.")
    return Signature.objects.create(strokes=strokes, signer_name=signer_name.strip())


def _check_checkout(
    borrower: Borrower, parts: Sequence[Part], *, quick_check: bool, gal_provided: bool
) -> None:
    """Pflichtangaben der Ausgabe wie im Altsystem."""
    if not quick_check:
        raise LendingError("Bitte die Kurzkontrolle bestätigen.")
    if not gal_provided:
        raise LendingError("Bitte „GAL bereitgestellt“ bestätigen.")
    contact = BorrowerContact.objects.filter(borrower=borrower).first()
    if contact is None:
        raise LendingError("Für die Ausgabe fehlen die Kontaktdaten des Entleihers.")
    if not contact.address.strip():
        raise LendingError("Für die Ausgabe ist die Adresse des Entleihers nötig.")
    if not (contact.email.strip() or contact.phone.strip()):
        raise LendingError("Für die Ausgabe ist E-Mail oder Telefon des Entleihers nötig.")
    missing = [str(part) for part in parts if not part.documents.exists()]
    if missing:
        raise LendingError(f"Für {', '.join(missing)} ist keine GAL hinterlegt.")


def _transition(loan: Loan, old: LoanStatus, new: LoanStatus) -> None:
    """Wechselt den Status nur, wenn er noch `old` ist (sicher bei gleichzeitigen Zugriffen)."""
    if Loan.objects.filter(pk=loan.pk, status=old).update(status=new) != 1:
        raise LendingError(f"{loan} ist nicht mehr „{old.label}“.")
    loan.status = new


def _require_status(loans: Sequence[Loan], status: LoanStatus) -> None:
    if not loans:
        raise LendingError("Keine Ausleihe ausgewählt.")
    wrong = [str(loan) for loan in loans if loan.status != status]
    if wrong:
        raise LendingError(f"Nicht „{status.label}“: {', '.join(wrong)}.")


# --- Vorgänge ---


def _book(
    borrower: Borrower,
    parts: Sequence[Part],
    start: date,
    end: date,
    *,
    actor: User | None,
    status: LoanStatus,
) -> Booking:
    period = _period(start, end)
    if not parts:
        raise LendingError("Keine Teile ausgewählt.")
    if len({part.pk for part in parts}) != len(parts):
        raise LendingError("Ein Teil ist mehrfach im selben Vorgang.")
    _lock_parts(parts)
    _check_available([(part, None) for part in parts], start, end)
    booking = Booking.objects.create(borrower=borrower, created_by=actor)
    for part in parts:
        try:
            with transaction.atomic():
                Loan.objects.create(booking=booking, part=part, period=period, status=status)
        except IntegrityError as error:
            raise LendingError(f"{part} ist im Zeitraum bereits vergeben.") from error
    return booking


@transaction.atomic
def reserve(
    borrower: Borrower, parts: Sequence[Part], start: date, end: date, *, actor: User | None
) -> Booking:
    """Reserviert Teile für [start, end] (Parität 12)."""
    require(actor, Right.LEND)
    return _book(borrower, parts, start, end, actor=actor, status=LoanStatus.RESERVED)


def _checked_out_events(
    loans: Sequence[Loan],
    borrower: Borrower,
    *,
    actor: User | None,
    quick_check: bool,
    gal_provided: bool,
    signature: Any,
    signer_name: str,
) -> None:
    signed = _signature(signature, signer_name)
    self_issued = actor is not None and borrower.user_id == actor.pk
    for loan in loans:
        LoanEvent.objects.create(
            loan=loan,
            kind=LoanEventKind.CHECKED_OUT,
            actor=actor,
            quick_check=quick_check,
            gal_provided=gal_provided,
            self_issued=self_issued,
            signature=signed,
        )


@transaction.atomic
def lend(
    borrower: Borrower,
    parts: Sequence[Part],
    start: date,
    end: date,
    *,
    actor: User | None,
    quick_check: bool,
    gal_provided: bool,
    signature: Any,
    signer_name: str,
) -> Booking:
    """Gibt Teile direkt aus, ohne vorherige Reservierung (Parität 12, 14)."""
    require(actor, Right.LEND)
    _check_checkout(borrower, parts, quick_check=quick_check, gal_provided=gal_provided)
    booking = _book(borrower, parts, start, end, actor=actor, status=LoanStatus.LENT)
    _checked_out_events(
        list(booking.loans.all()),
        borrower,
        actor=actor,
        quick_check=quick_check,
        gal_provided=gal_provided,
        signature=signature,
        signer_name=signer_name,
    )
    return booking


@transaction.atomic
def check_out(
    loans: Sequence[Loan],
    *,
    actor: User | None,
    quick_check: bool,
    gal_provided: bool,
    signature: Any,
    signer_name: str,
) -> None:
    """Wandelt Reservierungen in Ausleihen um, mit denselben Prüfungen (Parität 15)."""
    require(actor, Right.LEND)
    _require_status(loans, LoanStatus.RESERVED)
    borrowers = {loan.booking.borrower_id for loan in loans}
    if len(borrowers) != 1:
        raise LendingError("Eine Ausgabe gilt immer für genau einen Entleiher.")
    borrower = loans[0].booking.borrower
    parts = [loan.part for loan in loans]
    _check_checkout(borrower, parts, quick_check=quick_check, gal_provided=gal_provided)
    _lock_parts(parts)
    for loan in loans:
        _check_available([(loan.part, loan)], loan.start, loan.end)
    for loan in loans:
        _transition(loan, LoanStatus.RESERVED, LoanStatus.LENT)
    _checked_out_events(
        loans,
        borrower,
        actor=actor,
        quick_check=quick_check,
        gal_provided=gal_provided,
        signature=signature,
        signer_name=signer_name,
    )


@transaction.atomic
def return_loans(
    loans: Sequence[Loan],
    *,
    actor: User | None,
    quick_check: bool,
    signature: Any,
    signer_name: str,
    comment: str = "",
    lock_comment: str | None = None,
) -> None:
    """Nimmt Teile zurück; mit `lock_comment` wird das Teil zugleich gesperrt (Parität 17)."""
    require(actor, Right.LEND)
    if lock_comment is not None:
        require(actor, Right.LOCK)
    _require_status(loans, LoanStatus.LENT)
    signed = _signature(signature, signer_name)
    for loan in loans:
        _transition(loan, LoanStatus.LENT, LoanStatus.RETURNED)
        LoanEvent.objects.create(
            loan=loan,
            kind=LoanEventKind.RETURNED,
            actor=actor,
            quick_check=quick_check,
            comment=comment,
            signature=signed,
        )
        if lock_comment is not None and not loan.part.is_locked():
            record_event(loan.part, EventKind.LOCKED, actor=actor, comment=lock_comment)


@transaction.atomic
def cancel(loans: Sequence[Loan], *, actor: User | None, comment: str = "") -> None:
    """Storniert Reservierungen; der Zeitraum wird wieder frei.

    Verleiher stornieren nur selbst angelegte Reservierungen, ab Trainer/Materialwart alle.
    """
    require(actor, Right.LEND)
    for loan in loans:
        require(actor, Right.CHANGE_OTHERS_RESERVATIONS, loan.booking)
    _require_status(loans, LoanStatus.RESERVED)
    for loan in loans:
        _transition(loan, LoanStatus.RESERVED, LoanStatus.CANCELLED)
        LoanEvent.objects.create(
            loan=loan, kind=LoanEventKind.CANCELLED, actor=actor, comment=comment
        )
