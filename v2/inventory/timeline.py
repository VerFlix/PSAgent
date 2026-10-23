"""Zeitleiste je Teil (Parität 6): Vorgänge am Teil, wirksame Prüfungen und Verleih.

Entleiher stehen nur drin, wenn die Person verleihen darf; sonst nur der Vorgang.
"""

from dataclasses import dataclass
from datetime import datetime

from inventory.models import Part


@dataclass(frozen=True)
class Entry:
    at: datetime
    kind: str
    title: str
    detail: str
    actor: str
    booking_id: int | None = None


def actor_name(user: object, fallback: str = "") -> str:
    """Anzeigename der Person, die einen Vorgang erfasst hat; sonst `fallback`."""
    if user is None:
        return fallback
    full = getattr(user, "get_full_name", lambda: "")()
    return str(full or getattr(user, "username", fallback))


def timeline(part: Part, *, with_names: bool) -> list[Entry]:
    """Alle Einträge zum Teil, neueste zuerst."""
    from inspections.models import EFFECTIVE_KINDS, InspectionResult, RoundEvent
    from lending.models import LoanEvent

    entries = [
        Entry(
            event.at,
            "teil",
            event.get_kind_display(),
            event.comment,
            actor_name(event.actor, "Altsystem"),
        )
        for event in part.events.select_related("actor")
    ]

    effective = RoundEvent.objects.filter(kind__in=EFFECTIVE_KINDS).values("round_id")
    results = (
        InspectionResult.objects.filter(part=part, round_id__in=effective)
        .exclude(correction__round_id__in=effective)
        .select_related("round__inspector")
    )
    for result in results:
        detail = f"nächste Prüfung {result.next_due:%d.%m.%Y}" if result.next_due else ""
        if result.comment:
            detail = f"{detail} · {result.comment}" if detail else result.comment
        entries.append(
            Entry(
                result.recorded_at,
                "pruefung",
                f"PSA-Prüfung: {result.get_outcome_display()}",
                detail,
                result.round.inspector_display,
            )
        )

    loan_events = LoanEvent.objects.filter(loan__part=part).select_related(
        "actor", "loan__booking__borrower__contact"
    )
    for event in loan_events:
        loan = event.loan
        detail = f"{loan.start:%d.%m.%Y} – {loan.end:%d.%m.%Y}"
        if with_names:
            detail += f" · {loan.booking.borrower.display_name}"
        if event.comment:
            detail += f" · {event.comment}"
        entries.append(
            Entry(
                event.at,
                "verleih",
                f"Verleih: {event.get_kind_display()}",
                detail,
                actor_name(event.actor, event.actor_name or "Altsystem"),
                loan.booking_id if with_names else None,
            )
        )
    entries.sort(key=lambda entry: entry.at, reverse=True)
    return entries
