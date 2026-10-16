"""PSA im Browser: Liste „Nächste Prüfungen“ (Parität 6).

Die Liste enthält keine Personendaten und ist für jede angemeldete Rolle sichtbar. Gesperrte
Teile stehen oben, danach neue Teile ohne erste Prüfung, dann nach Fälligkeit.
"""

from datetime import date, timedelta
from typing import Any

from django import forms
from django.db.models import Prefetch
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET

from accounts.rights import Right, requires
from inspections.services import next_inspection_due
from inventory.models import Component, Part, PartStatus
from inventory.services import search_parts
from lending.services import today

DATE = forms.DateInput(attrs={"type": "date"})
STATUSES = [(value, label) for value, label in PartStatus.choices if value != PartStatus.RETIRED]


class DueForm(forms.Form):
    q = forms.CharField(label="Suche (Bezeichnung, EI, Seriennummer)", required=False)
    status = forms.ChoiceField(
        label="Status",
        required=False,
        choices=[
            ("", "alle im Bestand"),
            *STATUSES,
        ],
    )
    bis = forms.DateField(label="fällig bis", required=False, widget=DATE)
    schnell = forms.ChoiceField(
        label="Schnellwahl",
        required=False,
        choices=[("", "–"), ("30", "in den nächsten 30 Tagen"), ("ueberfaellig", "überfällig")],
    )


def _rank(row: dict[str, Any]) -> tuple[int, date, str]:
    """Gesperrt zuerst, dann ohne Prüfung, dann nach Fälligkeit; „keine PSA“ ans Ende."""
    if row["status"] == PartStatus.LOCKED:
        group = 0
    elif row["no_ppe"]:
        group = 3
    elif row["due"] is None:
        group = 1
    else:
        group = 2
    return (group, row["due"] or date.max, row["part"].name)


@requires(Right.VIEW_AVAILABILITY)
@require_GET
def next_due(request: HttpRequest) -> HttpResponse:
    """Parität 6: Liste „Nächste Prüfungen“ mit Filter; je Teil führt ein Link zur Zeitleiste."""
    form = DueForm(request.GET or None)
    data = form.cleaned_data if form.is_valid() else {}
    current = today()
    limit = data.get("bis")
    if data.get("schnell") == "30":
        limit = current + timedelta(days=30)
    elif data.get("schnell") == "ueberfaellig":
        limit = current - timedelta(days=1)

    parts = search_parts(
        Part.objects.prefetch_related(
            Prefetch("components", queryset=Component.objects.order_by("position")), "events"
        ),
        (data.get("q") or "").strip(),
    )
    rows = []
    for part in parts:
        status = part.status()
        if status == PartStatus.RETIRED or (data.get("status") and status != data["status"]):
            continue
        no_ppe = part.has_no_ppe()
        due = None if no_ppe else next_inspection_due(part)
        if limit is not None and (no_ppe or (due is not None and due > limit)):
            continue
        rows.append(
            {
                "part": part,
                "status": status,
                "no_ppe": no_ppe,
                "due": due,
                "days": (due - current).days if due is not None else None,
                "overdue_days": (current - due).days if due is not None and due < current else 0,
            }
        )
    rows.sort(key=_rank)
    context = {"form": form, "rows": rows, "today": current, "limit": limit}
    return render(request, "inspections/next_due.html", context)
