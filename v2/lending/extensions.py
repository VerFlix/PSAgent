"""Ausleihe verlängern (#129).

Festlegung (Felix, 01.11. und 04.11.2026):
- Verlängern dürfen alle ab Verleiher, auch vor der Fälligkeit und bei Überfälligkeit, ohne
  Unterschrift (auch am Telefon). Grund zum Antippen (telefonisch, persönlich, per E-Mail),
  Kommentar freiwillig. Bestätigung an den Entleiher per E-Mail auf Wunsch, wie die Quittung.
- Grenzen: Höchstzahl je Ausleihe und Höchstdauer je Verlängerung in Tagen; der Admin stellt sie
  ein (YubiKey, `ExtensionLimit`, nur angehängt). Ohne Eintrag 1 Verlängerung, höchstens 14 Tage.
- Für das neue Datum gelten dieselben Regeln wie bei der Ausgabe (`unavailability_reasons`, ohne
  die eigene Ausleihe): nicht gesperrt, PSA-Prüfung nach der Rückgabe, keine Überschneidung mit
  einer Reservierung oder Ausleihe (zusätzlich in der Datenbank ausgeschlossen).
- Der Zeitraum ändert sich nur hier; jede Verlängerung ist ein angehängter Eintrag
  (`LoanExtension`) in der Hash-Kette. Erinnerungen zählen ab dem neuen Datum neu (#72).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from django.conf import settings
from django.core.mail import EmailMessage
from django.db import transaction
from django.db.backends.postgresql.psycopg_any import DateRange
from django.template.loader import render_to_string

from accounts.models import User
from accounts.rights import Right, require
from lending import services
from lending.models import (
    BorrowerContact,
    ExtensionLimit,
    ExtensionReason,
    Loan,
    LoanExtension,
    LoanStatus,
)
from lending.services import LendingError
from privacy.notice import retention_notice

DEFAULT_COUNT = 1
DEFAULT_DAYS = 14


@dataclass(frozen=True)
class Limits:
    max_count: int
    max_days: int
    setting: ExtensionLimit | None = None


def limits() -> Limits:
    latest = ExtensionLimit.objects.first()
    if latest is None:
        return Limits(DEFAULT_COUNT, DEFAULT_DAYS)
    return Limits(latest.max_count, latest.max_days, latest)


def set_limits(max_count: int, max_days: int, *, actor: User | None) -> ExtensionLimit:
    """Neue Grenzen; nur Admin (die Ansicht verlangt zusätzlich den YubiKey)."""
    require(actor, Right.EXTENSION_LIMITS)
    if not 0 <= max_count <= 10:
        raise LendingError("Die Höchstzahl liegt zwischen 0 und 10 Verlängerungen.")
    if not 1 <= max_days <= 90:
        raise LendingError("Die Höchstdauer liegt zwischen 1 und 90 Tagen.")
    return ExtensionLimit.objects.create(max_count=max_count, max_days=max_days, actor=actor)


def problems(loan: Loan, new_end: date, rules: Limits | None = None) -> list[str]:
    """Gründe, warum `loan` nicht bis `new_end` verlängert werden kann; leer = möglich."""
    rules = rules or limits()
    today = services.today()
    if loan.status != LoanStatus.LENT:
        return ["ist nicht ausgeliehen"]
    reasons: list[str] = []
    done = loan.extensions.count()
    if done >= rules.max_count:
        reasons.append(f"wurde schon {done}-mal verlängert (höchstens {rules.max_count})")
    if new_end <= loan.end:
        reasons.append(f"ist ohnehin bis {loan.end:%d.%m.%Y} ausgeliehen")
    elif (new_end - loan.end).days > rules.max_days:
        reasons.append(f"darf höchstens {rules.max_days} Tage auf einmal verlängert werden")
    if new_end < today:
        reasons.append("das neue Datum liegt in der Vergangenheit")
    if not reasons:
        reasons += services.unavailability_reasons(loan.part, loan.start, new_end, ignore=loan)
    return reasons


@transaction.atomic
def extend(
    loans: Sequence[Loan],
    new_end: date,
    *,
    actor: User | None,
    reason: str,
    comment: str = "",
) -> list[LoanExtension]:
    """Verlängert die Ausleihen bis `new_end`; alle oder keine."""
    require(actor, Right.EXTEND)
    if reason not in ExtensionReason.values:
        raise LendingError("Bitte den Grund antippen (telefonisch, persönlich, per E-Mail).")
    if not loans:
        raise LendingError("Bitte mindestens ein Teil wählen.")
    rules = limits()
    locked = list(
        Loan.objects.select_for_update()
        .select_related("part")
        .filter(pk__in=[loan.pk for loan in loans])
        .order_by("pk")
    )
    errors = [
        f"{loan.part} {', '.join(found)}"
        for loan in locked
        if (found := problems(loan, new_end, rules))
    ]
    if errors:
        raise LendingError("; ".join(errors) + ".")
    created = []
    for loan in locked:
        # Erst der Eintrag, dann der Zeitraum: Die Datenbank lässt ein späteres Ende nur mit dem
        # passenden Eintrag zu (Trigger lending_loan_guard, Migration 0009).
        created.append(
            LoanExtension.objects.create(
                loan=loan,
                old_end=loan.end,
                new_end=new_end,
                reason=reason,
                comment=comment.strip(),
                actor=actor,
            )
        )
        loan.period = DateRange(loan.start, new_end, bounds="[]")
        loan.save(update_fields=["period"])
    return created


def confirmation(extensions: Sequence[LoanExtension]) -> EmailMessage | None:
    """Bestätigung an den Entleiher; None, wenn keine E-Mail-Adresse hinterlegt ist."""
    if not extensions:
        return None
    booking = extensions[0].loan.booking
    contact = BorrowerContact.objects.filter(borrower=booking.borrower).first()
    if contact is None or not contact.email.strip():
        return None
    context = {
        "name": contact.name,
        "club": settings.PSAGENT_CLUB_NAME,
        "booking": booking,
        "items": [
            {
                "extension": extension,
                "eis": ", ".join(str(c.ei) for c in extension.loan.part.components.all()),
            }
            for extension in extensions
        ],
        "notice": retention_notice(),
    }
    return EmailMessage(
        subject=f"Verlängerung bestätigt, Ausleihvorgang #{booking.pk}",
        body=render_to_string("mail/verlaengerung.txt", context),
        to=[contact.email.strip()],
    )
