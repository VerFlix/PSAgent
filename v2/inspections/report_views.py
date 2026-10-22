"""Prüfberichte im Browser (Parität 9, #39): Liste, Details, signiertes PDF, Prüfknopf.

Sehen dürfen alle Rollen (Felix, 21.10.2026; Recht „Prüfberichte sehen …“). Der Prüfknopf
ändert nichts; er prüft den Bericht neu (`inspections.reports.check`).
"""

import json
from typing import Any

from django import forms
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.utils.cache import add_never_cache_headers
from django.views.decorators.http import require_GET

from accounts.rights import Right, requires
from inspections import reports
from inspections.models import SignedProtocol
from legacy.models import LegacyReport
from lending.signatures import svg

DATE = forms.DateInput(attrs={"type": "date"})


class ReportFilter(forms.Form):
    start = forms.DateField(label="von", required=False, widget=DATE)
    end = forms.DateField(label="bis", required=False, widget=DATE)
    inspector = forms.CharField(label="Sachkundige*r", required=False, max_length=100)
    part = forms.CharField(label="Teil oder EI", required=False, max_length=100)


@requires(Right.REPORTS)
@require_GET
def report_list(request: HttpRequest) -> HttpResponse:
    form = ReportFilter(request.GET or None)
    filters: dict[str, Any] = {}
    if form.is_valid():
        filters = {key: value for key, value in form.cleaned_data.items() if value}
    entries = reports.all_reports(**filters)
    return render(
        request,
        "inspections/reports.html",
        {"form": form, "entries": entries, "limit": reports.LIST_LIMIT},
    )


def _signed(pk: int) -> SignedProtocol:
    return get_object_or_404(
        SignedProtocol.objects.select_related("round", "round__inspector", "certificate"),
        round_id=pk,
    )


@requires(Right.REPORTS)
@require_GET
def report_detail(request: HttpRequest, pk: int) -> HttpResponse:
    signed = _signed(pk)
    results = signed.round.current_results().select_related("part").order_by("part__name", "pk")
    return render(request, "inspections/report_detail.html", {"signed": signed, "results": results})


@requires(Right.REPORTS)
@require_GET
def report_pdf(request: HttpRequest, pk: int) -> HttpResponse:
    signed = _signed(pk)
    response = HttpResponse(bytes(signed.pdf), content_type="application/pdf")
    response["Content-Disposition"] = (
        f'attachment; filename="Pruefprotokoll_Runde_{signed.round_id}_signiert.pdf"'
    )
    add_never_cache_headers(response)
    return response


@requires(Right.REPORTS)
@require_GET
def report_check(request: HttpRequest, pk: int) -> HttpResponse:
    """Prüfknopf „unverändert?“; liefert nur den Ergebnis-Abschnitt (htmx)."""
    signed = _signed(pk)
    result = reports.check(signed)
    response = render(
        request, "inspections/_report_check.html", {"signed": signed, "result": result}
    )
    add_never_cache_headers(response)
    return response


@requires(Right.REPORTS)
@require_GET
def legacy_report_detail(request: HttpRequest, pk: int) -> HttpResponse:
    report = get_object_or_404(LegacyReport, pk=pk)
    try:
        strokes = json.loads(report.signature_data) if report.signature_data else None
    except ValueError:
        strokes = None
    return render(
        request,
        "inspections/legacy_report_detail.html",
        {
            "report": report,
            "items": report.items.select_related("part").order_by("legacy_id"),
            "signature": svg(strokes) if strokes else None,
        },
    )
