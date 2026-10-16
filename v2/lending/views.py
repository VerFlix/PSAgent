"""Verleih im Browser: Teile wählen, Entleiher, reservieren, direkt ausgeben (Parität 12–16).

Ablauf: Zeitraum und Filter → verfügbare Teile ankreuzen (nicht verfügbare mit Grund,
Parität 13) → Entleiher suchen oder neu anlegen → reservieren oder zur Ausgabe mit
Kurzkontrolle, „GAL bereitgestellt“ und Unterschrift. Am Vereinsgerät endet die Sitzung nach
dem Vorgang.

Rechte: alles ab Verleiher. Kontaktdaten sehen Verleiher nur bei laufenden Ausleihen; eingeben
oder überschreiben dürfen sie sie bei Reservierung und Ausgabe trotzdem, ohne die bisherigen
Werte zu sehen. Angezeigt wird nur, was fehlt und seit wann die Daten unverändert sind.
"""

from datetime import date
from typing import Any

from django.contrib import messages
from django.db import transaction
from django.db.models import Prefetch
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from accounts.devices import finish_operation
from accounts.models import User
from accounts.rights import Right, has_right, requires
from inventory.models import Component, Part, PartStatus
from inventory.services import search_parts
from lending import services
from lending.forms import BorrowerForm, CheckoutForm, PeriodForm
from lending.models import Booking, Borrower, BorrowerContact, Loan
from lending.services import LendingError


def _actor(request: HttpRequest) -> User:
    assert isinstance(request.user, User)  # noqa: S101 – von requires() geprüft
    return request.user


def _candidates(query: str) -> list[Part]:
    parts = Part.objects.prefetch_related(
        Prefetch("components", queryset=Component.objects.order_by("position")),
        "events",
        "documents",
    ).order_by("name", "id")
    return [part for part in search_parts(parts, query) if part.status() != PartStatus.RETIRED]


def _rows(start: date, end: date, query: str, chosen: set[int]) -> list[dict[str, Any]]:
    """Alle Teile im Bestand mit Verfügbarkeit im Zeitraum (Parität 13)."""
    return [
        {
            "part": part,
            "reasons": services.unavailability_reasons(part, start, end),
            "chosen": part.pk in chosen,
        }
        for part in _candidates(query)
    ]


def _chosen(request: HttpRequest) -> list[int]:
    return [int(value) for value in request.POST.getlist("teil") if value.isdigit()]


def _parts(ids: list[int]) -> list[Part]:
    return list(Part.objects.filter(pk__in=ids).prefetch_related("documents", "components"))


def _existing(form: BorrowerForm) -> Borrower | None:
    pk = form.cleaned_data.get("borrower")
    if not pk:
        return None
    borrower = Borrower.objects.filter(pk=pk, contact__isnull=False).first()
    if borrower is None:
        raise LendingError("Diesen Entleiher gibt es nicht (mehr).")
    return borrower


def _missing(form: BorrowerForm) -> list[str]:
    """Was für die Ausgabe fehlt, gespeicherte und neu eingegebene Werte zusammen."""
    new = {key: value.strip() for key, value in form.contact_fields().items()}
    borrower = _existing(form)
    stored = BorrowerContact.objects.filter(borrower=borrower).first() if borrower else None
    merged = BorrowerContact(
        name=new["name"] or (stored.name if stored else ""),
        address=new["address"] or (stored.address if stored else ""),
        email=new["email"] or (stored.email if stored else ""),
        phone=new["phone"] or (stored.phone if stored else ""),
    )
    return merged.gaps()


def _borrower(form: BorrowerForm, actor: User, *, checkout: bool) -> Borrower:
    """Bekannten Entleiher übernehmen (Daten ggf. überschreiben oder bestätigen) oder anlegen."""
    borrower = _existing(form)
    if borrower is None:
        fields = form.contact_fields()
        return services.create_borrower(
            fields["name"].strip(),
            address=fields["address"].strip(),
            email=fields["email"].strip(),
            phone=fields["phone"].strip(),
            actor=actor,
        )
    if form.has_new_values():
        services.overwrite_contact(borrower, actor=actor, **form.contact_fields())
    elif form.cleaned_data["contact_confirmed"]:
        services.confirm_contact(borrower, actor=actor)
    elif checkout:
        checked = BorrowerContact.objects.get(borrower=borrower).checked_at
        raise LendingError(
            f"Bitte nachfragen, ob sich die Kontaktdaten seit {checked:%d.%m.%Y} geändert "
            "haben, und „unverändert“ bestätigen oder die neuen Angaben eintragen."
        )
    return borrower


def _done(request: HttpRequest, booking: Booking, text: str) -> HttpResponse:
    if finish_operation(request):
        return redirect("accounts:login")
    messages.success(request, text)
    return redirect("lending:booking_detail", pk=booking.pk)


def _page(
    request: HttpRequest,
    period: PeriodForm,
    borrower_form: BorrowerForm,
    chosen: set[int],
    status: int = 200,
) -> HttpResponse:
    rows = []
    if period.is_valid():
        data = period.cleaned_data
        rows = _rows(data["start"], data["end"], data["q"].strip(), chosen)
    context: dict[str, Any] = {"period": period, "rows": rows, "borrower_form": borrower_form}
    selected = str(borrower_form.data.get("borrower", ""))
    if selected.isdigit():
        borrower = Borrower.objects.filter(pk=int(selected), contact__isnull=False).first()
        if borrower is not None:
            context.update(_status(request, borrower))
    return render(request, "lending/new.html", context, status=status)


def _status(request: HttpRequest, borrower: Borrower) -> dict[str, Any]:
    contact = BorrowerContact.objects.get(borrower=borrower)
    return {
        "selected": borrower,
        "contact": contact,
        "gaps": contact.gaps(),
        "may_view": has_right(request.user, Right.VIEW_CONTACTS, borrower),
    }


@requires(Right.LEND)
@require_http_methods(["GET", "POST"])
def new_booking(request: HttpRequest) -> HttpResponse:
    """Parität 12: reservieren oder direkt ausleihen, mehrere Teile, Zeitraum, Filter."""
    if request.method == "GET":
        period = PeriodForm(request.GET) if "start" in request.GET else None
        if period is None:
            period = PeriodForm(PeriodForm.initial_for_today())
        return _page(request, period, BorrowerForm(), set())

    period = PeriodForm(request.POST)
    borrower_form = BorrowerForm(request.POST)
    chosen = _chosen(request)
    if not (period.is_valid() and borrower_form.is_valid()):
        return _page(request, period, borrower_form, set(chosen), status=400)
    if not chosen:
        borrower_form.add_error(None, "Bitte mindestens ein Teil ankreuzen.")
        return _page(request, period, borrower_form, set(chosen), status=400)
    start, end = period.cleaned_data["start"], period.cleaned_data["end"]
    parts = _parts(chosen)
    actor = _actor(request)

    if request.POST.get("action") == "lend":
        try:
            if start != services.today():
                raise LendingError("Direkt ausgeben geht nur ab heute; sonst reservieren.")
            missing = _missing(borrower_form)
            if missing:
                raise LendingError(f"Für die Ausgabe fehlt: {', '.join(missing)}.")
            blocked = [
                f"{part} {', '.join(reasons)}"
                for part in parts
                if (reasons := services.unavailability_reasons(part, start, end))
            ]
            if blocked:
                raise LendingError("; ".join(blocked) + ".")
        except LendingError as error:
            borrower_form.add_error(None, str(error))
            return _page(request, period, borrower_form, set(chosen), status=400)
        context = {
            "period": period,
            "borrower_form": borrower_form,
            "parts": parts,
            "checkout": CheckoutForm(initial={"signer_name": _signer(borrower_form)}),
            "existing": _existing(borrower_form),
        }
        return render(request, "lending/checkout.html", context)

    try:
        with transaction.atomic():
            borrower = _borrower(borrower_form, actor, checkout=False)
            booking = services.reserve(borrower, parts, start, end, actor=actor)
    except LendingError as error:
        borrower_form.add_error(None, str(error))
        return _page(request, period, borrower_form, set(chosen), status=400)
    return _done(
        request, booking, f"Reserviert: {len(parts)} Teil(e), {start:%d.%m.} bis {end:%d.%m.%Y}."
    )


def _signer(form: BorrowerForm) -> str:
    name = str(form.cleaned_data["name"]).strip()
    if name:
        return name
    borrower = _existing(form)
    return borrower.display_name if borrower else ""


@requires(Right.LEND)
@require_POST
def checkout_new(request: HttpRequest) -> HttpResponse:
    """Parität 14: Ausgabe mit Kurzkontrolle, „GAL bereitgestellt“ und Unterschrift."""
    period = PeriodForm(request.POST)
    borrower_form = BorrowerForm(request.POST)
    checkout = CheckoutForm(request.POST)
    chosen = _chosen(request)
    if not (period.is_valid() and borrower_form.is_valid()) or not chosen:
        messages.error(request, "Die Auswahl ist unvollständig; bitte neu beginnen.")
        return redirect("lending:new_booking")
    parts = _parts(chosen)
    context: dict[str, Any] = {
        "period": period,
        "borrower_form": borrower_form,
        "parts": parts,
        "checkout": checkout,
        "existing": None,
    }
    try:
        context["existing"] = _existing(borrower_form)
        if not checkout.is_valid():
            return render(request, "lending/checkout.html", context, status=400)
        actor = _actor(request)
        data = checkout.cleaned_data
        with transaction.atomic():
            borrower = _borrower(borrower_form, actor, checkout=True)
            booking = services.lend(
                borrower,
                parts,
                period.cleaned_data["start"],
                period.cleaned_data["end"],
                actor=actor,
                quick_check=data["quick_check"],
                gal_provided=data["gal_provided"],
                signature=data["signature"],
                signer_name=data["signer_name"],
            )
    except LendingError as error:
        checkout.add_error(None, str(error))
        return render(request, "lending/checkout.html", context, status=400)
    return _done(request, booking, f"Ausgegeben: {len(parts)} Teil(e).")


@requires(Right.LEND)
@require_GET
def borrower_search(request: HttpRequest) -> HttpResponse:
    """htmx: Entleiher nach Namen, ab 3 Zeichen, höchstens 10; Verleiher sehen nur den Namen."""
    query = request.GET.get("suche", "")
    found = services.search_borrowers(query, actor=_actor(request))
    context = {
        "found": found,
        "too_short": len(query.strip()) < services.SEARCH_MIN_LENGTH,
        "limit": services.SEARCH_LIMIT,
    }
    return render(request, "lending/_borrower_results.html", context)


@requires(Right.LEND)
@require_GET
def borrower_status(request: HttpRequest, pk: int) -> HttpResponse:
    """htmx: was fehlt und seit wann die Kontaktdaten unverändert sind – ohne die Werte."""
    borrower = get_object_or_404(Borrower, pk=pk, contact__isnull=False)
    return render(request, "lending/_borrower_status.html", _status(request, borrower))


@requires(Right.LEND)
@require_GET
def booking_detail(request: HttpRequest, pk: int) -> HttpResponse:
    """Ausleihvorgang mit seinen Teilen; die volle Detailansicht folgt mit Parität 19."""
    booking = get_object_or_404(Booking.objects.select_related("borrower"), pk=pk)
    loans = Loan.objects.filter(booking=booking).select_related("part").order_by("part__name")
    return render(request, "lending/booking_detail.html", {"booking": booking, "loans": loans})
