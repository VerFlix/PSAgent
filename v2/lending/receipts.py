"""Quittung per E-Mail nach Ausgabe oder Rückgabe, auf Wunsch (#72, Konzept Abschnitt 6).

Inhalt: Teile mit EI, Zeitraum, Links zur GAL, Vorgangsnummer und Datenschutzhinweis; keine
Unterschrift. Die Mail wird nicht gespeichert; fehlt sie, lässt sie sich erneut senden.
"""

import logging
from datetime import timedelta
from enum import StrEnum

from django.conf import settings
from django.core.mail import EmailMessage
from django.template.loader import render_to_string
from django.utils import timezone

from accounts.models import User
from accounts.rights import Right, has_right
from lending.models import Booking, BorrowerContact, LoanEvent, LoanEventKind
from privacy.notice import retention_notice

logger = logging.getLogger(__name__)


class ReceiptKind(StrEnum):
    CHECKOUT = "ausgabe"
    RETURN = "rueckgabe"


EVENT = {
    ReceiptKind.CHECKOUT: LoanEventKind.CHECKED_OUT,
    ReceiptKind.RETURN: LoanEventKind.RETURNED,
}
TITLE = {ReceiptKind.CHECKOUT: "Ausgabe", ReceiptKind.RETURN: "Rückgabe"}


# Wer die Kontaktdaten nicht (mehr) sehen darf, sendet nur die Quittung zu einem eigenen Vorgang
# nach, und nur so lange.
RESEND_WINDOW = timedelta(hours=24)


class ReceiptError(RuntimeError):
    """Die Quittung ließ sich nicht erstellen oder senden; der Text sagt, warum."""


def _events(booking: Booking, kind: ReceiptKind) -> list[LoanEvent]:
    return list(
        LoanEvent.objects.filter(loan__booking=booking, kind=EVENT[kind])
        .select_related("loan__part")
        .prefetch_related("loan__part__components", "loan__part__documents")
        .order_by("at", "loan__part__name")
    )


def build(booking: Booking, kind: ReceiptKind) -> EmailMessage | None:
    """Die Quittung; `None`, wenn keine E-Mail-Adresse hinterlegt ist."""
    contact = BorrowerContact.objects.filter(borrower=booking.borrower).first()
    if contact is None or not contact.email.strip():
        return None
    events = _events(booking, kind)
    if not events:
        raise ReceiptError(
            "Noch nichts ausgegeben."
            if kind == ReceiptKind.CHECKOUT
            else "Noch nichts zurückgenommen."
        )
    context = {
        "kind": kind,
        "title": TITLE[kind],
        "booking": booking,
        "name": contact.name,
        "club": settings.PSAGENT_CLUB_NAME,
        "events": [
            {
                "event": event,
                "at": timezone.localtime(event.at),
                "eis": ", ".join(str(c.ei) for c in event.loan.part.components.all()),
                "gal": [doc for doc in event.loan.part.documents.all() if doc.url],
            }
            for event in events
        ],
        "notice": retention_notice(),
    }
    return EmailMessage(
        subject=f"Quittung: {TITLE[kind]}, Ausleihvorgang #{booking.pk}",
        body=render_to_string("mail/quittung.txt", context),
        to=[contact.email.strip()],
    )


def may_resend(user: User, booking: Booking, kind: ReceiptKind) -> bool:
    """Erneut senden darf, wer die Kontaktdaten des Vorgangs sehen darf, oder wer die Ausgabe
    bzw. Rückgabe selbst erfasst hat, innerhalb von `RESEND_WINDOW`. Sonst ließen sich Mails an
    beliebige Entleiher auslösen."""
    if has_right(user, Right.VIEW_CONTACTS, booking):
        return True
    return LoanEvent.objects.filter(
        loan__booking=booking,
        kind=EVENT[kind],
        actor=user,
        at__gte=timezone.now() - RESEND_WINDOW,
    ).exists()


def send(booking: Booking, kind: ReceiptKind) -> str:
    """Sendet die Quittung und gibt die Meldung für die Oberfläche zurück (ohne Adresse)."""
    message = build(booking, kind)
    if message is None:
        raise ReceiptError("Keine Quittung: Für den Entleiher ist keine E-Mail-Adresse hinterlegt.")
    try:
        message.send()
    except Exception as error:
        logger.warning(
            "Quittung für Vorgang #%s nicht gesendet: %s", booking.pk, type(error).__name__
        )
        raise ReceiptError(
            "Die Quittung konnte nicht gesendet werden. Bitte später erneut senden."
        ) from error
    return "Quittung per E-Mail gesendet."
