"""Personendaten eines Entleihers löschen (#63, Konzept Abschnitte 4 und 7).

Gelöscht werden Kontaktdaten, Name und Strichliste aller Unterschriften zu seinen Ausleihen
und die Scans seiner Ausleihformulare aus dem Notbetrieb. Die Ausleihhistorie bleibt mit den
Prüfsummen vollständig; der Entleiher erscheint danach als „Entleiher #<Nummer>“.

Löschfrist (Festlegung Felix, 17.10.2026, Vorschlag an den Vorstand): `PSAGENT_RETENTION_YEARS`
Jahre nach Ende des Jahres der letzten Aktivität, also der letzten Reservierung, Ausgabe,
Rückgabe oder Stornierung. Nie gelöscht wird bei offener Ausleihe, offenem Klärungsfall aus
dem Notbetrieb oder Löschsperre.

Freitext (Kommentare an Vorgängen) bleibt, weil er PSA-relevant sein kann; die Formulare bitten,
dort keine Namen einzutragen.
"""

from dataclasses import dataclass
from datetime import date

from django.conf import settings
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from accounts.models import User
from accounts.rights import Right, require
from emergency.backfill import borrowers_of, erase_scans, open_cases
from lending.models import ACTIVE_STATUSES, Borrower, Loan
from lending.services import erase_personal_data
from privacy.models import Erasure, ErasureHold


class PrivacyError(ValueError):
    """Löschen oder Löschsperre verstößt gegen eine Regel."""


@dataclass(frozen=True)
class Due:
    """Ein Entleiher, dessen Löschfrist abgelaufen ist."""

    borrower: Borrower
    last_activity: date


def today() -> date:
    return timezone.localdate()


def erasure_date(last_activity: date) -> date:
    """Erster Tag nach der Frist: 1. Januar nach Ablauf der Jahre ab Ende des Aktivitätsjahres."""
    return date(last_activity.year + settings.PSAGENT_RETENTION_YEARS + 1, 1, 1)


def last_activity(borrower: Borrower) -> date:
    moments = [
        borrower.created_at,
        *Borrower.objects.filter(pk=borrower.pk)
        .aggregate(booking=Max("bookings__created_at"), event=Max("bookings__loans__events__at"))
        .values(),
    ]
    return max(timezone.localdate(moment) for moment in moments if moment is not None)


def hold_of(borrower: Borrower) -> ErasureHold | None:
    """Die geltende Löschsperre, sonst `None`."""
    return ErasureHold.current(borrower.pk)


def _open_case_borrowers() -> set[int]:
    return {
        borrower
        for case in open_cases()
        if case.form is not None
        for borrower in borrowers_of(case.form)
    }


def blockers(borrower: Borrower, *, waiting: set[int] | None = None) -> list[str]:
    """Warum der Entleiher jetzt nicht gelöscht werden darf; leer = darf."""
    reasons = []
    if Erasure.objects.filter(borrower=borrower).exists():
        reasons.append("Der Entleiher ist bereits gelöscht.")
    if Loan.objects.filter(booking__borrower=borrower, status__in=ACTIVE_STATUSES).exists():
        reasons.append("Es gibt offene Ausleihen oder Reservierungen.")
    if hold_of(borrower) is not None:
        reasons.append("Für den Entleiher gilt eine Löschsperre.")
    if borrower.pk in (_open_case_borrowers() if waiting is None else waiting):
        reasons.append("Ein Formular aus dem Notbetrieb wartet auf die Klärungsliste.")
    return reasons


def erase_borrower(borrower: Borrower, *, actor: User | None, by_deadline: bool = False) -> Erasure:
    """Löscht alle Personendaten des Entleihers. Nur Admin; `None` = Verwaltungsbefehl."""
    require(actor, Right.MANAGE_USERS)
    with transaction.atomic():
        Borrower.objects.select_for_update().filter(pk=borrower.pk).first()
        if reasons := blockers(borrower):
            raise PrivacyError(" ".join(reasons))
        activity = last_activity(borrower)
        erase_personal_data(borrower, actor=actor)
        erase_scans(borrower, actor=actor)
        return Erasure.objects.create(
            borrower=borrower, actor=actor, by_deadline=by_deadline, last_activity=activity
        )


def due_borrowers(on: date) -> list[Due]:
    """Entleiher, deren Löschfrist bis `on` abgelaufen ist und die gelöscht werden dürfen."""
    waiting = _open_case_borrowers()
    due = []
    for borrower in Borrower.objects.filter(erasure__isnull=True).order_by("pk"):
        activity = last_activity(borrower)
        if erasure_date(activity) <= on and not blockers(borrower, waiting=waiting):
            due.append(Due(borrower, activity))
    return due


def run_retention(on: date | None = None, *, dry_run: bool = False) -> list[Due]:
    """Täglicher Lauf: löscht alle fälligen Entleiher; im Probelauf nur die Liste."""
    due = due_borrowers(on or today())
    if dry_run:
        return due
    erased = []
    for entry in due:
        try:
            erase_borrower(entry.borrower, actor=None, by_deadline=True)
        except PrivacyError:
            continue  # inzwischen wieder aktiv; der nächste Lauf prüft erneut
        erased.append(entry)
    return erased


def set_hold(borrower: Borrower, reason: str, *, actor: User | None) -> ErasureHold:
    """Löschsperre setzen, etwa für einen Schadensfall; Pflichtgrund."""
    require(actor, Right.ERASURE_HOLD)
    return _hold(borrower, reason, active=True, actor=actor)


def release_hold(borrower: Borrower, reason: str, *, actor: User | None) -> ErasureHold:
    """Löschsperre aufheben; die Frist läuft dann wieder normal."""
    require(actor, Right.ERASURE_HOLD)
    if hold_of(borrower) is None:
        raise PrivacyError("Für diesen Entleiher gilt keine Löschsperre.")
    return _hold(borrower, reason, active=False, actor=actor)


def _hold(borrower: Borrower, reason: str, *, active: bool, actor: User | None) -> ErasureHold:
    if not reason.strip():
        raise PrivacyError("Bitte einen Grund angeben.")
    if Erasure.objects.filter(borrower=borrower).exists():
        raise PrivacyError("Der Entleiher ist bereits gelöscht.")
    return ErasureHold.objects.create(
        borrower=borrower, active=active, reason=reason.strip(), actor=actor
    )
