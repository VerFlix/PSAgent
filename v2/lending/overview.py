"""Übersicht anstehender Ausleihen (Parität 18) und Kalender mit Verfügbarkeitssuche (20).

Rechte: Die Übersicht nennt Entleiher und ist daher erst ab Verleiher sichtbar. Den Kalender
sieht jede Rolle („Verfügbarkeit und Kalender sehen, ohne Personendaten“); Namen und die
Suche nach dem Namen erscheinen erst ab Verleiher.
"""

import calendar
from datetime import date, timedelta
from typing import Any

from django import forms
from django.db.backends.postgresql.psycopg_any import DateRange
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET

from accounts.rights import Right, has_right, requires
from inventory.models import Part
from inventory.services import search_parts
from lending import reminders, services
from lending.models import ACTIVE_STATUSES, Loan, LoanStatus

WEEKDAYS = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")
MONTHS = (
    "Januar", "Februar", "März", "April", "Mai", "Juni",
    "Juli", "August", "September", "Oktober", "November", "Dezember",
)  # fmt: skip


def _loans() -> Any:
    return Loan.objects.select_related("part", "booking__borrower__contact")


@requires(Right.LEND)
@require_GET
def overview(request: HttpRequest) -> HttpResponse:
    """Parität 18: anstehende Ausleihen mit „überfällig“-Hinweis."""
    today = services.today()
    lent = list(_loans().filter(status=LoanStatus.LENT))
    lent.sort(key=lambda loan: (loan.end, loan.part.name))
    reserved = list(_loans().filter(status=LoanStatus.RESERVED))
    reserved.sort(key=lambda loan: (loan.start, loan.part.name))
    context = {
        "today": today,
        "overdue": [loan for loan in lent if loan.end < today],
        "due_today": [loan for loan in lent if loan.end == today],
        "lent": [loan for loan in lent if loan.end > today],
        "reserved": reserved,
        "reminders_on": reminders.enabled(),
        "may_switch_reminders": has_right(request.user, Right.REMINDERS),
    }
    return render(request, "lending/overview.html", context)


DATE = forms.DateInput(attrs={"type": "date"})


class CalendarForm(forms.Form):
    ansicht = forms.ChoiceField(
        label="Ansicht", choices=[("monat", "Monat"), ("woche", "Woche")], required=False
    )
    datum = forms.DateField(
        label="Datum", required=False, widget=forms.DateInput(attrs={"type": "date"})
    )
    q = forms.CharField(label="Teil (Bezeichnung, EI, Seriennummer)", required=False)
    name = forms.CharField(label="Entleiher (Name)", required=False)
    von = forms.DateField(
        label="frei von", required=False, widget=forms.DateInput(attrs={"type": "date"})
    )
    bis = forms.DateField(
        label="bis", required=False, widget=forms.DateInput(attrs={"type": "date"})
    )


def _range(view: str, anchor: date) -> tuple[date, date]:
    if view == "woche":
        start = anchor - timedelta(days=anchor.weekday())
        return start, start + timedelta(days=6)
    last = calendar.monthrange(anchor.year, anchor.month)[1]
    return anchor.replace(day=1), anchor.replace(day=last)


def _shift(view: str, anchor: date, step: int) -> date:
    if view == "woche":
        return anchor + timedelta(days=7 * step)
    month = anchor.month - 1 + step
    return date(anchor.year + month // 12, month % 12 + 1, 1)


@requires(Right.VIEW_AVAILABILITY)
@require_GET
def calendar_view(request: HttpRequest) -> HttpResponse:
    """Parität 20: Monats- und Wochenansicht mit Tagesliste und Verfügbarkeitssuche."""
    form = CalendarForm(request.GET or None)
    data = form.cleaned_data if form.is_valid() else {}
    today = services.today()
    view = data.get("ansicht") or "monat"
    anchor = data.get("datum") or today
    first, last = _range(view, anchor)
    with_names = has_right(request.user, Right.LEND)

    loans = _loans().filter(
        status__in=ACTIVE_STATUSES, period__overlap=DateRange(first, last, bounds="[]")
    )
    query = (data.get("q") or "").strip()
    if query:
        parts = search_parts(Part.objects.all(), query)
        loans = loans.filter(part__in=parts)
    name = (data.get("name") or "").strip() if with_names else ""
    if name:
        loans = loans.filter(booking__borrower__contact__name__icontains=name)
    loans = sorted(loans, key=lambda loan: (loan.start, loan.part.name))

    days = []
    day = first - timedelta(days=first.weekday())
    end = last + timedelta(days=6 - last.weekday())
    while day <= end:
        on_day = [loan for loan in loans if loan.start <= day <= loan.end]
        days.append(
            {
                "date": day,
                "inside": first <= day <= last,
                "today": day == today,
                "loans": on_day,
                "overdue": any(loan.is_overdue(today) for loan in on_day),
            }
        )
        day += timedelta(days=1)
    weeks = [days[index : index + 7] for index in range(0, len(days), 7)]

    selected = data.get("datum") if request.GET.get("tag") else None
    context: dict[str, Any] = {
        "form": form,
        "view": view,
        "anchor": anchor,
        "first": first,
        "last": last,
        "title": f"{MONTHS[anchor.month - 1]} {anchor.year}"
        if view == "monat"
        else f"Woche {first:%d.%m.} – {last:%d.%m.%Y}",
        "weeks": weeks,
        "weekdays": WEEKDAYS,
        "previous": _shift(view, anchor, -1),
        "next": _shift(view, anchor, 1),
        "with_names": with_names,
        "query": query,
        "name": name,
        "day": next((d for d in days if d["date"] == selected), None) if selected else None,
        "availability": _availability(data, query),
    }
    return render(request, "lending/calendar.html", context)


def _availability(data: dict[str, Any], query: str) -> dict[str, Any] | None:
    """Verfügbarkeitssuche: alle Teile im Bestand mit Grund, falls im Zeitraum nicht frei."""
    start, end = data.get("von"), data.get("bis")
    if not start or not end:
        return None
    if end < start:
        return {"error": "„bis“ liegt vor „von“.", "rows": []}
    parts = search_parts(Part.objects.prefetch_related("components", "events"), query)
    rows = [
        {"part": part, "reasons": services.unavailability_reasons(part, start, end)}
        for part in parts.order_by("name", "pk")
        if not part.is_retired()
    ]
    return {"start": start, "end": end, "rows": rows}
