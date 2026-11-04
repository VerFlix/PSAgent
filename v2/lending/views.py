"""Verleih im Browser: Teile wählen, Entleiher, reservieren, direkt ausgeben (Parität 12–16).

Ablauf: Zeitraum und Filter → verfügbare Teile ankreuzen (nicht verfügbare mit Grund,
Parität 13) → Entleiher suchen oder neu anlegen → reservieren oder zur Ausgabe mit
Kurzkontrolle, „GAL bereitgestellt“ und Unterschrift. Am Vereinsgerät endet die Sitzung nach
dem Vorgang.

Rechte: alles ab Verleiher. Kontaktdaten sehen Verleiher nur bei laufenden Ausleihen; eingeben
oder überschreiben dürfen sie sie bei Reservierung und Ausgabe trotzdem, ohne die bisherigen
Werte zu sehen. Angezeigt wird nur, was fehlt und seit wann die Daten unverändert sind.
"""

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Prefetch, prefetch_related_objects
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from accounts.confirm import requires_yubikey
from accounts.devices import finish_operation
from accounts.models import User
from accounts.rights import Right, has_right, requires
from inventory.models import Component, Part, PartStatus
from inventory.services import search_parts
from lending import digest, extensions, members, receipts, reminders, services
from lending.forms import (
    BorrowerForm,
    CancelForm,
    CheckoutForm,
    ExtensionForm,
    ExtensionLimitForm,
    MemberCardForm,
    PeriodForm,
    PersonForm,
    ReturnForm,
)
from lending.models import (
    Booking,
    Borrower,
    BorrowerContact,
    DigestRun,
    ExtensionLimit,
    Loan,
    LoanEvent,
    LoanExtension,
    LoanStatus,
    Reminder,
)
from lending.receipts import ReceiptKind
from lending.services import LendingError
from lending.signatures import svg
from privacy.models import Erasure, ErasureHold

logger = logging.getLogger(__name__)


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
            member_number=form.cleaned_data.get("member_number") or "",
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
    card = _looks_like_card(query)
    if card:
        # Gescannter oder eingetippter Mitgliedsausweis (#125): genau diese Person.
        holder = members.find(query, actor=_actor(request))
        found = [holder] if holder is not None else []
    context = {
        "card": card,
        "found": found,
        "too_short": len(query.strip()) < services.SEARCH_MIN_LENGTH,
        "limit": services.SEARCH_LIMIT,
    }
    return render(request, "lending/_borrower_results.html", context)


def _looks_like_card(query: str) -> bool:
    try:
        members.normalize(query)
    except LendingError:
        return False
    return True


@requires(Right.CREATE_BORROWER)
@require_http_methods(["GET", "POST"])
def borrower_create(request: HttpRequest) -> HttpResponse:
    """Person anlegen, auch ohne Ausleihe, mit Mitgliedsausweis (#125)."""
    form = PersonForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            services.create_borrower(
                data["name"].strip(),
                address=data["address"].strip(),
                email=data["email"].strip(),
                phone=data["phone"].strip(),
                member_number=data["member_number"],
                actor=_actor(request),
            )
        except LendingError as error:
            form.add_error(None, str(error))
        else:
            messages.success(
                request,
                f"{data['name'].strip()} ist angelegt. Die Kontaktdaten siehst du künftig nur bei "
                "laufender Ausleihe.",
            )
            return redirect("lending:overview")
    return render(request, "lending/borrower_create.html", {"form": form})


@requires(Right.LINK_MEMBER_CARD)
@require_http_methods(["GET", "POST"])
def member_card(request: HttpRequest, pk: int) -> HttpResponse:
    """Mitgliedsausweis verbinden (ab Verleiher, mit Namensabgleich) bzw. ändern oder lösen."""
    borrower = get_object_or_404(Borrower, pk=pk, contact__isnull=False)
    actor = _actor(request)
    may_change = has_right(actor, Right.CHANGE_MEMBER_CARD)
    form = MemberCardForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        raw = form.cleaned_data["member_number"]
        try:
            if may_change and request.POST.get("aktion") == "aendern":
                members.change(borrower, raw, actor=actor)
            elif not form.cleaned_data["name_checked"]:
                raise LendingError("Bitte zuerst den Namen auf dem Ausweis vergleichen.")
            else:
                members.link(borrower, raw, actor=actor)
        except LendingError as error:
            form.add_error(None, str(error))
        else:
            messages.success(request, "Mitgliedsausweis gespeichert.")
            return redirect("lending:member_card", pk=borrower.pk)
    contact = BorrowerContact.objects.get(borrower=borrower)
    context = {
        "borrower": borrower,
        "contact": contact,
        "form": form,
        "may_change": may_change,
        "may_view": has_right(actor, Right.VIEW_CONTACTS, borrower),
    }
    return render(request, "lending/member_card.html", context)


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
    prefetch_related_objects(loans, "extensions__actor")
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
        "may_extend": has_right(request.user, Right.EXTEND),
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
        "reminders_on": reminders.booking_enabled(booking),
        "sent_reminders": Reminder.objects.filter(loan__booking=booking).select_related(
            "loan__part"
        ),
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


@dataclass
class ExtensionPage:
    """Ergebnis von `extension_page`: Kontext zum Anzeigen oder die angelegten Verlängerungen."""

    context: dict[str, Any]
    created: list[LoanExtension]
    confirm: bool = False


def extension_page(request: HttpRequest, booking: Booking) -> ExtensionPage:
    """Verlängern (#129), gemeinsam für PC und Theke: Teile wählen, neues Datum, Grund."""
    lent = [loan for loan in _loans(booking) if loan.status == LoanStatus.LENT]
    rules = extensions.limits()
    latest = max((loan.end for loan in lent), default=services.today())
    start = max(latest, services.today())
    proposal = start + timedelta(days=min(7, rules.max_days))
    form = ExtensionForm(request.POST or None, initial={"new_end": proposal})
    chosen = {loan.pk for loan in lent}
    if request.method == "POST":
        chosen = {int(v) for v in request.POST.getlist("ausleihe") if v.isdigit()}
    created: list[LoanExtension] = []
    if request.method == "POST" and form.is_valid():
        try:
            created = extensions.extend(
                [loan for loan in lent if loan.pk in chosen],
                form.cleaned_data["new_end"],
                actor=_actor(request),
                reason=form.cleaned_data["reason"],
                comment=form.cleaned_data["comment"],
            )
        except LendingError as error:
            form.add_error(None, str(error))
    rows = [{"loan": loan, "done": loan.extensions.count()} for loan in lent]
    context = {"booking": booking, "rows": rows, "chosen": chosen, "form": form, "rules": rules}
    confirm = bool(created) and bool(form.cleaned_data.get("confirm"))
    return ExtensionPage(context, created, confirm)


def send_extension_confirmation(created: list[LoanExtension]) -> tuple[int, str]:
    """Bestätigung an den Entleiher; ein Fehler wird gemeldet, die Verlängerung bleibt gültig."""
    message = extensions.confirmation(created)
    if message is None:
        return messages.WARNING, "Keine Bestätigung: Für den Entleiher ist keine E-Mail hinterlegt."
    try:
        message.send()
    except Exception as error:
        logger.warning("Bestätigung der Verlängerung nicht gesendet: %s", type(error).__name__)
        return messages.WARNING, "Die Bestätigung ließ sich nicht senden."
    return messages.SUCCESS, "Bestätigung per E-Mail gesendet."


def extension_text(created: list[LoanExtension]) -> str:
    count = len(created)
    return f"Verlängert: {count} Teil{'e' if count != 1 else ''} bis {created[0].new_end:%d.%m.%Y}."


@requires(Right.EXTEND)
@require_http_methods(["GET", "POST"])
def booking_extend(request: HttpRequest, pk: int) -> HttpResponse:
    """Ausleihe verlängern (#129), ohne Unterschrift, im Rahmen der Grenzen."""
    booking = _booking(pk)
    page = extension_page(request, booking)
    if not page.context["rows"] and not page.created:
        messages.error(request, "In diesem Vorgang ist nichts ausgeliehen.")
        return redirect("lending:booking_detail", pk=booking.pk)
    if not page.created:
        status = 400 if request.method == "POST" else 200
        return render(request, "lending/booking_extend.html", page.context, status=status)
    note = send_extension_confirmation(page.created) if page.confirm else None
    logged_out = finish_operation(request)
    messages.success(request, extension_text(page.created))
    if note is not None:
        messages.add_message(request, *note)
    if logged_out:
        return redirect("accounts:login")
    return redirect("lending:booking_detail", pk=booking.pk)


@requires(Right.EXTENSION_LIMITS)
@requires_yubikey
@require_http_methods(["GET", "POST"])
def extension_limits(request: HttpRequest) -> HttpResponse:
    """Grenzen für Verlängerungen (#129): nur Admin, jede Änderung mit YubiKey."""
    current = extensions.limits()
    form = ExtensionLimitForm(
        request.POST or None,
        initial={"max_count": current.max_count, "max_days": current.max_days},
    )
    if request.method == "POST" and form.is_valid():
        try:
            extensions.set_limits(
                form.cleaned_data["max_count"], form.cleaned_data["max_days"], actor=_actor(request)
            )
        except LendingError as error:
            form.add_error(None, str(error))
        else:
            messages.success(request, "Grenzen für Verlängerungen gespeichert.")
            return redirect("lending:extension_limits")
    context = {
        "form": form,
        "current": current,
        "history": ExtensionLimit.objects.select_related("actor")[:10],
    }
    status = 400 if request.method == "POST" else 200
    return render(request, "lending/extension_limits.html", context, status=status)


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
def booking_reminders(request: HttpRequest, pk: int) -> HttpResponse:
    """Erinnerungen für diesen Vorgang aus- oder wieder einschalten (#72)."""
    booking = _booking(pk)
    on = request.POST.get("an") == "1"
    reminders.set_booking_enabled(booking, on, actor=_actor(request))
    state = "eingeschaltet" if on else "ausgeschaltet"
    messages.success(request, f"Erinnerungen für diesen Vorgang {state}.")
    return redirect("lending:booking_detail", pk=booking.pk)


@requires(Right.REMINDERS)
@require_POST
def reminder_switch(request: HttpRequest) -> HttpResponse:
    """Erinnerungen für alle ein- oder ausschalten (nur Admin)."""
    on = request.POST.get("an") == "1"
    reminders.set_enabled(on, actor=_actor(request))
    messages.success(request, f"E-Mail-Erinnerungen für alle {'an' if on else 'aus'}.")
    return redirect("lending:overview")


@requires(Right.OVERDUE_DIGEST)
@requires_yubikey
@require_http_methods(["GET", "POST"])
def digest_settings(request: HttpRequest) -> HttpResponse:
    """Sammelmail zu überfälligen Ausleihen (#128): Schalter und Empfänger, nur mit YubiKey."""
    setting = digest.current()
    error = ""
    on = setting.enabled if setting else False
    text = "\n".join(setting.recipients) if setting else ""
    if request.method == "POST":
        on = request.POST.get("an") == "on"
        text = request.POST.get("empfaenger", "")
        try:
            digest.configure(on, text.splitlines(), actor=_actor(request))
        except digest.DigestError as problem:
            error = str(problem)
        else:
            messages.success(request, f"Sammelmail {'an' if on else 'aus'}, Empfänger gespeichert.")
            return redirect("lending:digest_settings")
    context = {
        "setting": setting,
        "on": on,
        "text": text,
        "error": error,
        "maximum": digest.MAX_RECIPIENTS,
        "runs": DigestRun.objects.all()[:10],
    }
    return render(request, "lending/digest_settings.html", context)


@requires(Right.OVERDUE_DIGEST)
@require_POST
def digest_test(request: HttpRequest) -> HttpResponse:
    """Testmail ohne Personendaten an die eingetragenen Empfänger."""
    try:
        count = digest.send_test(actor=_actor(request))
    except digest.DigestError as problem:
        messages.error(request, str(problem))
    else:
        messages.success(request, f"Testmail an {count} Adresse(n) gesendet.")
    return redirect("lending:digest_settings")


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
