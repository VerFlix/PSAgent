"""Erinnerung bei Überfälligkeit per E-Mail (#72, Parität 18).

Takt (Festlegung Felix, 17.10.2026): am ersten Tag nach der geplanten Rückgabe, dann nach 7
und nach 14 Tagen; höchstens 3 Mails je Ausleihe und je Lauf höchstens eine, auch wenn der
tägliche Lauf (`manage.py erinnerungen`) einige Tage ausfiel. Eine Mail je Ausleihvorgang.

Abschaltbar für alle (nur Admin) und je Vorgang (ab Verleiher, etwa wenn der Entleiher schon
angerufen hat). Gespeichert wird nur, welche Erinnerung wann rausging (`Reminder`).
"""

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta

from django.conf import settings
from django.core.mail import EmailMessage
from django.template.loader import render_to_string

from accounts.models import User
from accounts.rights import Right, require
from lending import services
from lending.models import (
    Booking,
    BorrowerContact,
    Loan,
    LoanStatus,
    Reminder,
    ReminderSwitch,
)
from privacy.notice import retention_notice

logger = logging.getLogger(__name__)

# Tage nach dem letzten Ausleihtag, ab denen die 1., 2. und 3. Erinnerung fällig ist.
OFFSETS = (1, 8, 15)


@dataclass(frozen=True)
class Due:
    """Fällige Erinnerung zu einem Vorgang: die Ausleihen und die jeweilige Nummer."""

    booking: Booking
    loans: list[tuple[Loan, int]]

    @property
    def number(self) -> int:
        return max(number for _, number in self.loans)


def enabled() -> bool:
    latest = ReminderSwitch.objects.filter(booking__isnull=True).first()
    return latest is None or latest.enabled


def set_enabled(on: bool, *, actor: User | None) -> ReminderSwitch:
    require(actor, Right.REMINDERS)
    return ReminderSwitch.objects.create(enabled=on, actor=actor)


def booking_enabled(booking: Booking) -> bool:
    latest = ReminderSwitch.objects.filter(booking=booking).first()
    return latest is None or latest.enabled


def set_booking_enabled(booking: Booking, on: bool, *, actor: User | None) -> ReminderSwitch:
    require(actor, Right.LEND)
    return ReminderSwitch.objects.create(booking=booking, enabled=on, actor=actor)


def _next_number(loan: Loan, sent: int, on: date) -> int | None:
    number = sent + 1
    if number > len(OFFSETS) or on < loan.end + timedelta(days=OFFSETS[number - 1]):
        return None
    return number


def due(on: date) -> list[Due]:
    """Fällige Erinnerungen am Tag `on`, je Vorgang; ohne Rücksicht auf Schalter und Adresse."""
    loans = (
        Loan.objects.filter(status=LoanStatus.LENT)
        .select_related("booking", "part")
        .prefetch_related("reminders", "part__components")
        .order_by("booking_id", "part__name", "pk")
    )
    grouped: dict[int, list[tuple[Loan, int]]] = defaultdict(list)
    bookings: dict[int, Booking] = {}
    for loan in loans:
        number = _next_number(loan, len(loan.reminders.all()), on)
        if number is not None:
            grouped[loan.booking_id].append((loan, number))
            bookings[loan.booking_id] = loan.booking
    return [Due(bookings[pk], entries) for pk, entries in grouped.items()]


def build(entry: Due) -> EmailMessage | None:
    contact = BorrowerContact.objects.filter(borrower=entry.booking.borrower).first()
    if contact is None or not contact.email.strip():
        return None
    context = {
        "name": contact.name,
        "club": settings.PSAGENT_CLUB_NAME,
        "booking": entry.booking,
        "number": entry.number,
        "maximum": len(OFFSETS),
        "loans": [
            {"loan": loan, "eis": ", ".join(str(c.ei) for c in loan.part.components.all())}
            for loan, _ in entry.loans
        ],
        "notice": retention_notice(),
    }
    return EmailMessage(
        subject=f"Erinnerung: Rückgabe überfällig, Ausleihvorgang #{entry.booking.pk}",
        body=render_to_string("mail/erinnerung.txt", context),
        to=[contact.email.strip()],
    )


def run(on: date | None = None, *, dry_run: bool = False) -> list[Due]:
    """Täglicher Lauf: sendet fällige Erinnerungen; gibt die gesendeten (im Probelauf: fälligen)
    zurück. Ein Versandfehler wird protokolliert; der nächste Lauf versucht es erneut."""
    if not enabled():
        return []
    result = []
    for entry in due(on or services.today()):
        if not booking_enabled(entry.booking):
            continue
        message = build(entry)
        if message is None:
            continue
        if dry_run:
            result.append(entry)
            continue
        try:
            message.send()
        except Exception as error:
            logger.warning(
                "Erinnerung zu Vorgang #%s nicht gesendet: %s",
                entry.booking.pk,
                type(error).__name__,
            )
            continue
        Reminder.objects.bulk_create(
            Reminder(loan=loan, number=number) for loan, number in entry.loans
        )
        result.append(entry)
    return result
