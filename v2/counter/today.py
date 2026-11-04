"""Theke (#127, Teil 3): „Heute“ mit Abholungen, Rückgaben und Bestand.

Festlegung (Felix, 31.10. und 03.11.2026): Abholungen, Rückgaben (überfällige zuerst) und der
Bestand der Arten im Schnellzugriff. Bei Arten „nach Größe“ je Größe: heute frei, verliehen,
gesperrt; ein verstellbarer Gurt zählt bei jeder Größe, die er abdeckt (wie bei der Auswahl).
"""

from dataclasses import dataclass

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET

from accounts.rights import Right, requires
from counter import kinds, stock
from counter.kinds import Kind
from counter.returns import _open_bookings
from counter.views import WANTED, _wanted_label, pickup_rows
from inventory.sizes import size_label, sizes_covering
from lending import services
from lending.models import Loan, LoanStatus

FREE, LENT, LOCKED = "frei", "verliehen", "gesperrt"


@dataclass(frozen=True)
class StockRow:
    label: str
    aria: str
    free: int
    lent: int
    locked: int


def _states(kind: Kind) -> list[tuple[str, str | None]]:
    """(Größe, Zustand) je nicht ausgesondertem Teil der Art; Zustand None = aus anderem Grund
    heute nicht ausleihbar (z. B. PSA-Prüfung fällig, neu, heute reserviert)."""
    parts = [p for p in kinds.parts_of(kind).prefetch_related("components") if not p.is_retired()]
    lent = set(
        Loan.objects.filter(status=LoanStatus.LENT, part__in=parts).values_list(
            "part_id", flat=True
        )
    )
    today = services.today()
    result: list[tuple[str, str | None]] = []
    for part in parts:
        component = stock._component(part, kind)
        size = component.size if component else ""
        if part.is_locked():
            state: str | None = LOCKED
        elif part.pk in lent:
            state = LENT
        else:
            state = None if stock.reasons(part, today) else FREE
        result.append((size, state))
    return result


def _row(label: str, aria: str, states: list[str | None]) -> StockRow:
    return StockRow(label, aria, states.count(FREE), states.count(LENT), states.count(LOCKED))


def stock_rows(kind: Kind) -> list[StockRow]:
    entries = _states(kind)
    if not kind.sized:
        return [_row(kind.label, kind.label, [state for _, state in entries])]
    rows = []
    for wanted in WANTED:
        fitting = [
            state
            for size, state in entries
            if (size == "" if wanted == stock.NO_SIZE else size in sizes_covering(wanted))
        ]
        label = size_label(wanted) if wanted else "ohne"
        rows.append(_row(label, f"{kind.label} {_wanted_label(wanted)}", fitting))
    return rows


@requires(Right.LEND)
@require_GET
def today_view(request: HttpRequest) -> HttpResponse:
    """Heute: Abholungen, fällige und überfällige Rückgaben, Bestand im Schnellzugriff."""
    today = services.today()
    context = {
        "today": today,
        "pickups": pickup_rows(),
        "returns": [row for row in _open_bookings() if row.due <= today],
        "stock": [(kind, stock_rows(kind)) for kind in kinds.quick_kinds()],
    }
    return render(request, "counter/today.html", context)
