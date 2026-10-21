"""Prüfmodus im Browser (Parität 7): Runde beginnen, Teile scannen, Ergebnisse, abschließen.

Festlegungen (Felix, 19.10.2026): je Sachkundigem eine offene Runde, die der Prüfmodus fortsetzt;
gescannte Teile kommen zuerst auf die Liste; jedes Teil wird einzeln geprüft und einzeln mit
eigenem Ergebnis bestätigt, ohne Sammelbestätigung und ohne vorausgewähltes Ergebnis;
gescannt wird mit der Kamera (`static/js/pruefmodus.js`) oder über das Eingabefeld (auch für
Handscanner). Abgeschlossene Runden wirken erst mit der Signatur (#38).

Alle Aktionen in der Runde antworten auf htmx-Anfragen mit dem Abschnitt der Runde, damit die
Kamera beim Scannen weiterläuft; ohne JavaScript leiten sie zur Seite zurück.
"""

from typing import Any
from urllib.parse import unquote

from django import forms
from django.contrib import messages
from django.db.models import Exists, OuterRef
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.cache import add_never_cache_headers
from django.views.decorators.http import require_GET, require_POST

from accounts.models import User
from accounts.rights import Right, requires
from inspections import protocol, services
from inspections.models import (
    InspectionRound,
    Outcome,
    RoundEvent,
    RoundEventKind,
    RoundProtocol,
)
from inspections.services import InspectionError
from inspections.signature import MAX_BYTES, SignatureError
from inventory.models import Part

DATE = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")


class ResultForm(forms.Form):
    outcome = forms.ChoiceField(label="Ergebnis", choices=Outcome.choices, widget=forms.RadioSelect)
    comment = forms.CharField(label="Kommentar", required=False, max_length=2000)
    next_due = forms.DateField(label="Nächste Prüfung", required=False, widget=DATE)


def _actor(request: HttpRequest) -> User:
    assert isinstance(request.user, User)  # noqa: S101 – von requires() geprüft
    return request.user


def _own_round(request: HttpRequest, pk: int) -> InspectionRound:
    """Nur die eigene Runde; fremde gibt es für diese Person nicht (404)."""
    return get_object_or_404(InspectionRound, pk=pk, inspector=_actor(request))


def scanned_ei(value: str) -> str:
    """EI aus dem Scan: der QR-Code enthält `…/q/<EI>`, ein Handscanner liest dasselbe."""
    text = value.strip()
    marker = text.rfind("/q/")
    return unquote(text[marker + 3 :]) if marker >= 0 else text


def _context(round_: InspectionRound) -> dict[str, Any]:
    suggestion = services.suggest_next_due(12)
    return {
        "round": round_,
        "pending": services.pending_parts(round_),
        "results": list(round_.current_results()),
        "outcomes": Outcome.choices,
        "suggestion": suggestion,
        "suggestion_short": services.suggest_next_due(6),
    }


def _answer(request: HttpRequest, round_: InspectionRound) -> HttpResponse:
    if request.headers.get("HX-Request") == "true":
        return render(request, "inspections/_round.html", _context(round_))
    return redirect("inspections:round_mode")


def _run(request: HttpRequest, action: Any, success: str | None = None) -> None:
    try:
        result = action()
    except (InspectionError, SignatureError) as error:
        messages.error(request, str(error))
    else:
        if success:
            messages.success(request, success.format(result=result))


@requires(Right.INSPECT)
@require_GET
def round_mode(request: HttpRequest) -> HttpResponse:
    """Prüfmodus: die offene Runde, sonst der Knopf zum Beginnen und die letzten Runden."""
    actor = _actor(request)
    round_ = services.open_round(actor)
    closed = (
        InspectionRound.objects.filter(inspector=actor, events__kind=RoundEventKind.CLOSED)
        .exclude(events__kind__in=[RoundEventKind.SIGNED, RoundEventKind.CANCELLED])
        .annotate(has_protocol=Exists(RoundProtocol.objects.filter(round=OuterRef("pk"))))
        .order_by("-started_at")[:10]
    )
    context: dict[str, Any] = {"round": None, "unsigned": closed}
    if round_ is not None:
        context.update(_context(round_))
    return render(request, "inspections/round.html", context)


@requires(Right.INSPECT)
@require_POST
def round_start(request: HttpRequest) -> HttpResponse:
    _run(request, lambda: services.start_round(actor=_actor(request)), "{result} begonnen.")
    return redirect("inspections:round_mode")


@requires(Right.INSPECT)
@require_POST
def round_scan(request: HttpRequest, pk: int) -> HttpResponse:
    round_ = _own_round(request, pk)
    ei = scanned_ei(request.POST.get("ei", ""))
    if ei:
        _run(
            request,
            lambda: services.collect_by_ei(round_, ei, actor=_actor(request)),
            "{result} hinzugefügt.",
        )
    return _answer(request, round_)


@requires(Right.INSPECT)
@require_POST
def part_to_round(request: HttpRequest, part_pk: int) -> HttpResponse:
    """Von der Teileseite (etwa nach einem Scan mit der Kamera-App) in die offene Runde."""
    actor = _actor(request)
    part = get_object_or_404(Part, pk=part_pk)

    def add() -> Part:
        round_ = services.open_round(actor) or services.start_round(actor=actor)
        services.collect(round_, part, actor=actor)
        return part

    _run(request, add, "{result} zur Prüfungsrunde hinzugefügt.")
    return redirect("inspections:round_mode")


@requires(Right.INSPECT)
@require_POST
def round_drop(request: HttpRequest, pk: int, part_pk: int) -> HttpResponse:
    round_ = _own_round(request, pk)
    part = get_object_or_404(Part, pk=part_pk)
    _run(request, lambda: services.drop(round_, part, actor=_actor(request)))
    return _answer(request, round_)


@requires(Right.INSPECT)
@require_POST
def round_result(request: HttpRequest, pk: int, part_pk: int) -> HttpResponse:
    round_ = _own_round(request, pk)
    part = get_object_or_404(Part, pk=part_pk)
    form = ResultForm(request.POST)
    if not form.is_valid():
        messages.error(request, f"{part}: Bitte Ergebnis und gültiges Datum angeben.")
        return _answer(request, round_)
    data = form.cleaned_data
    outcome = data["outcome"]
    next_due = None if outcome == Outcome.RETIRE else data["next_due"]
    _run(
        request,
        lambda: services.set_result(
            round_, part, outcome, actor=_actor(request), comment=data["comment"], next_due=next_due
        ),
        f"{part}: {Outcome(outcome).label}.",
    )
    return _answer(request, round_)


@requires(Right.INSPECT)
@require_POST
def round_close(request: HttpRequest, pk: int) -> HttpResponse:
    round_ = _own_round(request, pk)

    def close() -> RoundEvent:
        return services.close_round(round_, actor=_actor(request))

    _run(
        request,
        close,
        f"{round_} abgeschlossen. Sie wirkt auf die Teile, sobald sie signiert ist.",
    )
    return redirect("inspections:round_mode")


@requires(Right.INSPECT)
@require_GET
def round_protocol(request: HttpRequest, pk: int) -> HttpResponse:
    """Prüfprotokoll zum Signieren; nur für den Prüfer der Runde (#38)."""
    round_ = _own_round(request, pk)
    entry = get_object_or_404(RoundProtocol, round=round_)
    response = HttpResponse(bytes(entry.pdf), content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{protocol.filename(round_)}"'
    add_never_cache_headers(response)
    return response


@requires(Right.INSPECT)
@require_POST
def round_sign(request: HttpRequest, pk: int) -> HttpResponse:
    """Signiertes Prüfprotokoll hochladen; nur der Prüfer der Runde (#38)."""
    round_ = _own_round(request, pk)
    upload = request.FILES.get("signed_pdf")
    if upload is None:
        messages.error(request, "Bitte das signierte Prüfprotokoll (PDF) auswählen.")
    elif upload.size is None or upload.size > MAX_BYTES:
        messages.error(request, "Die Datei ist zu groß für ein Prüfprotokoll.")
    else:
        data = upload.read()
        _run(
            request,
            lambda: services.sign_round(round_, actor=_actor(request), signed_pdf=data),
            f"{round_} signiert. Die Ergebnisse wirken jetzt auf die Teile.",
        )
    return redirect("inspections:round_mode")


@requires(Right.INSPECT)
@require_POST
def round_cancel(request: HttpRequest, pk: int) -> HttpResponse:
    round_ = _own_round(request, pk)
    comment = request.POST.get("comment", "")
    _run(
        request,
        lambda: services.cancel_round(round_, actor=_actor(request), comment=comment),
        f"{round_} abgebrochen.",
    )
    return redirect("inspections:round_mode")
