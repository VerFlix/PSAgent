"""Tägliche Sammelmail über überfällige Ausleihen an Vereinsadressen (#128).

Festlegung (Felix, 01.11.2026):

- Einmal täglich, nur wenn etwas überfällig ist; seit gestern neu Überfällige sind markiert.
- Inhalt: Vorgangsnummer, Name des Entleihers, offene Teile mit EI, Tage überfällig, Link zum
  Vorgang. Keine Telefonnummer, E-Mail oder Adresse; die stehen in PSAgent beim Vorgang.
- Der Admin schaltet die Mail und pflegt die Empfänger (jede Änderung mit YubiKey, in der
  Ansicht geprüft) und kann eine Testmail ohne Personendaten senden.
- „Erinnerungen für diesen Vorgang aus“ betrifft nur Mails an den Entleiher, nicht diese.

Je Empfänger geht eine eigene Mail raus, damit niemand die anderen Adressen sieht. Gespeichert
wird nur, an welchem Tag wie viele Vorgänge an wie viele Adressen gingen (`DigestRun`). Scheitert
der Versand, wird nichts gespeichert und der Lauf meldet einen Fehler; der nächste versucht es
erneut.
"""

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date

from django.conf import settings
from django.template.loader import render_to_string

from accounts.models import User
from accounts.rights import Right, require
from lending import services
from lending.models import Booking, DigestRun, DigestSetting, Loan, LoanStatus
from mailer.digests import MAX_RECIPIENTS as MAX_RECIPIENTS
from mailer.digests import DigestError as DigestError
from mailer.digests import absolute_link, send_each
from mailer.digests import clean_recipients as clean_recipients


@dataclass
class Item:
    name: str
    eis: str
    end: date


@dataclass
class Entry:
    """Ein überfälliger Vorgang mit seinen noch offenen Teilen."""

    booking: Booking
    name: str
    days: int
    link: str
    items: list[Item] = field(default_factory=list)

    @property
    def new(self) -> bool:
        return self.days == 1


@dataclass(frozen=True)
class Result:
    sent: bool
    bookings: int = 0
    recipients: int = 0
    reason: str = ""


def current() -> DigestSetting | None:
    return DigestSetting.objects.first()


def recipients() -> list[str]:
    setting = current()
    return list(setting.recipients) if setting else []


def enabled() -> bool:
    setting = current()
    return bool(setting and setting.enabled and setting.recipients)


def configure(on: bool, addresses: Sequence[str], *, actor: User | None) -> DigestSetting:
    """Neuer Stand von Schalter und Empfängern; nur Admin (die Ansicht verlangt den YubiKey)."""
    require(actor, Right.OVERDUE_DIGEST)
    cleaned = clean_recipients(addresses)
    if on and not cleaned:
        raise DigestError("Mindestens eine Adresse eintragen, sonst lässt sich nichts senden.")
    return DigestSetting.objects.create(enabled=on, recipients=cleaned, actor=actor)


def overdue(on: date) -> list[Entry]:
    """Vorgänge mit Teilen, die am Tag `on` noch draußen sind und deren Rückgabe vorbei ist."""
    loans = (
        Loan.objects.filter(status=LoanStatus.LENT)
        .select_related("booking__borrower", "part")
        .prefetch_related("part__components")
        .order_by("booking_id", "part__name", "pk")
    )
    grouped: dict[int, list[Loan]] = defaultdict(list)
    for loan in loans:
        if loan.end < on:
            grouped[loan.booking_id].append(loan)
    entries = []
    for group in grouped.values():
        booking = group[0].booking
        entry = Entry(
            booking=booking,
            name=booking.borrower.display_name,
            days=max((on - loan.end).days for loan in group),
            link=absolute_link("lending:booking_detail", booking.pk),
        )
        for loan in group:
            eis = ", ".join(str(c.ei) for c in loan.part.components.all())
            entry.items.append(Item(name=loan.part.name, eis=eis, end=loan.end))
        entries.append(entry)
    return sorted(entries, key=lambda e: (-e.days, e.booking.pk))


def _subject(count: int, on: date) -> str:
    noun = "überfälliger Ausleihvorgang" if count == 1 else "überfällige Ausleihvorgänge"
    return f"PSAgent: {count} {noun} ({on:%d.%m.%Y})"


def run(on: date | None = None, *, dry_run: bool = False) -> Result:
    """Täglicher Lauf. Sendet höchstens eine Sammelmail je Tag; im Probelauf nur zählen."""
    day = on or services.today()
    if not enabled():
        return Result(sent=False, reason="aus")
    if DigestRun.objects.filter(day=day).exists():
        return Result(sent=False, reason="heute schon gesendet")
    entries = overdue(day)
    addresses = recipients()
    if not entries:
        return Result(sent=False, reason="nichts überfällig")
    if dry_run:
        return Result(sent=False, bookings=len(entries), recipients=len(addresses))
    context = {
        "on": day,
        "club": settings.PSAGENT_CLUB_NAME,
        "entries": entries,
        "count_text": "1 Vorgang" if len(entries) == 1 else f"{len(entries)} Vorgänge",
        "settings_link": absolute_link("lending:digest_settings"),
    }
    body = render_to_string("mail/sammelmail.txt", context)
    subject = _subject(len(entries), day)
    send_each(subject, body, addresses)
    DigestRun.objects.create(day=day, bookings=len(entries), recipients=len(addresses))
    return Result(sent=True, bookings=len(entries), recipients=len(addresses))


def send_test(*, actor: User | None) -> int:
    """Testmail ohne Personendaten an die eingetragenen Empfänger; gibt deren Zahl zurück."""
    require(actor, Right.OVERDUE_DIGEST)
    addresses = recipients()
    if not addresses:
        raise DigestError("Es sind keine Empfänger eingetragen.")
    context = {
        "club": settings.PSAGENT_CLUB_NAME,
        "settings_link": absolute_link("lending:digest_settings"),
    }
    body = render_to_string("mail/sammelmail_test.txt", context)
    subject = "PSAgent: Testmail der Sammelmail zu überfälligen Ausleihen"
    send_each(subject, body, addresses)
    return len(addresses)
