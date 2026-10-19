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
from django.core.exceptions import PermissionDenied
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
from lending import receipts, services
from lending.forms import BorrowerForm, CancelForm, CheckoutForm, PeriodForm, ReturnForm
from lending.models import Booking, Borrower, BorrowerContact, Loan, LoanEvent, LoanStatus
from lending.receipts import ReceiptKind
from lending.services import LendingError
from lending.signatures import svg
from privacy.models import Erasure, ErasureHold


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


def _done(
    request: HttpRequest, booking: Booking, text: str, receipt: ReceiptKind | None = None
) -> HttpResponse:
    note = _send_receipt(booking, receipt) if receipt is not None else None
    if finish_operation(request):
        return redirect("accounts:login")
    messages.success(request, text)
    if note is not None:
        messages.add_message(request, *note)
    return redirect("lending:booking_detail", pk=booking.pk)


def _send_receipt(booking: Booking, kind: ReceiptKind) -> tuple[int, str]:
    """Quittung senden; ein Fehler wird gemeldet, der Vorgang bleibt gültig (#72)."""
    try:
        return messages.SUCCESS, receipts.send(booking, kind)
    except receipts.ReceiptError as error:
        return messages.WARNING, str(error)


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
        # Vorauswahl über den Link „Reservieren oder ausgeben“ der Teileseite (QR-Scan, #14).
        preselected = {int(value) for value in request.GET.getlist("teil") if value.isdigit()}
        return _page(request, period, BorrowerForm(), preselected)

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
    receipt = ReceiptKind.CHECKOUT if data["receipt"] else None
    return _done(request, booking, f"Ausgegeben: {len(parts)} Teil(e).", receipt)


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


def _booking(pk: int) -> Booking:
    return get_object_or_404(Booking.objects.select_related("borrower"), pk=pk)


def _loans(booking: Booking) -> list[Loan]:
    events = LoanEvent.objects.select_related("actor", "signature__content").order_by("at", "id")
    return list(
        Loan.objects.filter(booking=booking)
        .select_related("part")
        .prefetch_related(Prefetch("events", queryset=events))
        .order_by("part__name", "pk")
    )


def _selected(request: HttpRequest, loans: list[Loan], status: LoanStatus) -> list[Loan]:
    ids = {int(value) for value in request.POST.getlist("ausleihe") if value.isdigit()}
    return [loan for loan in loans if loan.pk in ids and loan.status == status]


def _event_row(event: LoanEvent) -> dict[str, Any]:
    """Vorgang mit Unterschrift; Name und Striche fehlen, wenn sie gelöscht sind (#63)."""
    content = event.signature.stored_content if event.signature else None
    return {
        "event": event,
        "content": content,
        "signature": svg(content.strokes) if content else None,
    }


@requires(Right.LEND)
@require_GET
def booking_detail(request: HttpRequest, pk: int) -> HttpResponse:
    """Parität 19: Ausleihvorgang mit Vorgängen und beiden Unterschriften."""
    booking = _booking(pk)
    loans = _loans(booking)
    today = services.today()
    rows = [
        {
            "loan": loan,
            "overdue": loan.is_overdue(today),
            "events": [_event_row(event) for event in loan.events.all()],
        }
        for loan in loans
    ]
    contact = None
    if has_right(request.user, Right.VIEW_CONTACTS, booking):
        contact = BorrowerContact.objects.filter(borrower=booking.borrower).first()
    context = {
        "booking": booking,
        "rows": rows,
        "contact": contact,
        "reserved": [loan for loan in loans if loan.status == LoanStatus.RESERVED],
        "lent": [loan for loan in loans if loan.status == LoanStatus.LENT],
        "may_cancel": has_right(request.user, Right.CHANGE_OTHERS_RESERVATIONS, booking),
        "may_hold": has_right(request.user, Right.ERASURE_HOLD),
        "receipts": [
            kind
            for kind in ReceiptKind
            if any(
                event.kind == receipts.EVENT[kind] for loan in loans for event in loan.events.all()
            )
            and receipts.may_resend(_actor(request), booking, kind)
        ],
        "hold": ErasureHold.current(booking.borrower_id),
        "erased": Erasure.objects.filter(borrower=booking.borrower).exists(),
        "contact_known": BorrowerContact.objects.filter(borrower=booking.borrower).exists(),
        "cancel_form": CancelForm(),
    }
    return render(request, "lending/booking_detail.html", context)


@requires(Right.LEND)
@require_http_methods(["GET", "POST"])
def booking_checkout(request: HttpRequest, pk: int) -> HttpResponse:
    """Parität 15: Reservierung in Ausleihe umwandeln, mit denselben Prüfungen wie die Ausgabe."""
    booking = _booking(pk)
    loans = _loans(booking)
    reserved = [loan for loan in loans if loan.status == LoanStatus.RESERVED]
    if not reserved:
        messages.error(request, "In diesem Vorgang ist nichts mehr reserviert.")
        return redirect("lending:booking_detail", pk=booking.pk)
    if not BorrowerContact.objects.filter(borrower=booking.borrower).exists():
        messages.error(request, "Die Kontaktdaten dieses Entleihers sind gelöscht.")
        return redirect("lending:booking_detail", pk=booking.pk)
    data = request.POST if request.method == "POST" else None
    borrower_form = BorrowerForm(data, initial={"borrower": booking.borrower_id})
    checkout = CheckoutForm(data, initial={"signer_name": booking.borrower.display_name})
    context: dict[str, Any] = {
        "booking": booking,
        "reserved": reserved,
        "chosen": {loan.pk for loan in reserved},
        "borrower_form": borrower_form,
        "checkout": checkout,
        **_status(request, booking.borrower),
    }
    if request.method == "GET":
        return render(request, "lending/booking_checkout.html", context)
    chosen = _selected(request, loans, LoanStatus.RESERVED)
    context["chosen"] = {loan.pk for loan in chosen}
    if not (borrower_form.is_valid() and checkout.is_valid()):
        return render(request, "lending/booking_checkout.html", context, status=400)
    try:
        if not chosen:
            raise LendingError("Bitte mindestens ein reserviertes Teil wählen.")
        if borrower_form.cleaned_data["borrower"] != booking.borrower_id:
            raise LendingError("Der Entleiher gehört nicht zu diesem Vorgang.")
        actor = _actor(request)
        with transaction.atomic():
            _borrower(borrower_form, actor, checkout=True)
            services.check_out(
                chosen,
                actor=actor,
                quick_check=checkout.cleaned_data["quick_check"],
                gal_provided=checkout.cleaned_data["gal_provided"],
                signature=checkout.cleaned_data["signature"],
                signer_name=checkout.cleaned_data["signer_name"],
            )
    except LendingError as error:
        checkout.add_error(None, str(error))
        return render(request, "lending/booking_checkout.html", context, status=400)
    receipt = ReceiptKind.CHECKOUT if checkout.cleaned_data["receipt"] else None
    return _done(request, booking, f"Ausgegeben: {len(chosen)} Teil(e).", receipt)


@requires(Right.LEND)
@require_http_methods(["GET", "POST"])
def booking_return(request: HttpRequest, pk: int) -> HttpResponse:
    """Parität 17: Rückgabe mit Kommentar, Kurzprüfung, Unterschrift; einzelne Teile sperren."""
    booking = _booking(pk)
    loans = _loans(booking)
    lent = [loan for loan in loans if loan.status == LoanStatus.LENT]
    if not lent:
        messages.error(request, "In diesem Vorgang ist nichts ausgeliehen.")
        return redirect("lending:booking_detail", pk=booking.pk)
    data = request.POST if request.method == "POST" else None
    form = ReturnForm(data, initial={"signer_name": booking.borrower.display_name})
    context: dict[str, Any] = {
        "booking": booking,
        "lent": lent,
        "chosen": {loan.pk for loan in lent},
        "locked": set(),
        "form": form,
    }
    if request.method == "GET":
        return render(request, "lending/booking_return.html", context)
    chosen = _selected(request, loans, LoanStatus.LENT)
    lock = {int(value) for value in request.POST.getlist("sperren") if value.isdigit()}
    lock &= {loan.pk for loan in chosen}
    context.update(chosen={loan.pk for loan in chosen}, locked=lock)
    if not form.is_valid():
        return render(request, "lending/booking_return.html", context, status=400)
    reason = form.cleaned_data["lock_reason"].strip()
    try:
        if not chosen:
            raise LendingError("Bitte mindestens ein Teil wählen.")
        if lock and not reason:
            raise LendingError("Bitte den Grund der Sperre angeben.")
        services.return_loans(
            chosen,
            actor=_actor(request),
            quick_check=True,
            signature=form.cleaned_data["signature"],
            signer_name=form.cleaned_data["signer_name"],
            comment=form.cleaned_data["comment"],
            lock_comment=reason if lock else None,
            lock=lock,
        )
    except LendingError as error:
        form.add_error(None, str(error))
        return render(request, "lending/booking_return.html", context, status=400)
    text = f"Zurückgenommen: {len(chosen)} Teil(e)"
    if lock:
        text += f", davon {len(lock)} gesperrt"
    receipt = ReceiptKind.RETURN if form.cleaned_data["receipt"] else None
    return _done(request, booking, text + ".", receipt)


@requires(Right.LEND)
@require_POST
def booking_receipt(request: HttpRequest, pk: int) -> HttpResponse:
    """Quittung (erneut) senden, z. B. wenn der Versand bei der Ausgabe fehlschlug (#72)."""
    booking = _booking(pk)
    try:
        kind = ReceiptKind(request.POST.get("art", ""))
    except ValueError:
        messages.error(request, "Unbekannte Quittung.")
        return redirect("lending:booking_detail", pk=booking.pk)
    if not receipts.may_resend(_actor(request), booking, kind):
        raise PermissionDenied(
            "Quittungen sendet nach, wer die Kontaktdaten sehen darf oder den Vorgang selbst "
            "erfasst hat (24 Stunden)."
        )
    messages.add_message(request, *_send_receipt(booking, kind))
    return redirect("lending:booking_detail", pk=booking.pk)


@requires(Right.LEND)
@require_POST
def booking_cancel(request: HttpRequest, pk: int) -> HttpResponse:
    """Reservierungen stornieren: Verleiher nur eigene, ab Trainer/Materialwart alle."""
    booking = _booking(pk)
    chosen = _selected(request, _loans(booking), LoanStatus.RESERVED)
    form = CancelForm(request.POST)
    form.is_valid()
    if not chosen:
        messages.error(request, "Bitte mindestens eine Reservierung wählen.")
        return redirect("lending:booking_detail", pk=booking.pk)
    try:
        services.cancel(chosen, actor=_actor(request), comment=form.cleaned_data.get("comment", ""))
    except LendingError as error:
        messages.error(request, str(error))
        return redirect("lending:booking_detail", pk=booking.pk)
    return _done(request, booking, f"Storniert: {len(chosen)} Reservierung(en).")
