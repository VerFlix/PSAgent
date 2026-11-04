"""Theke (#127, Teil 2): Rückgabe und Schaden melden.

Festlegung (Felix, 31.10. und 03.11.2026):
- Rückgabe: Liste aller offenen Ausleihen, überfällige zuerst, auch vorzeitige. Die Ausleihe
  wird am gescannten Teil erkannt; Teilrückgabe ist möglich. Pflichten wie am PC
  (Kurzprüfung, Name, Unterschrift; Parität 17). Ein Schaden sperrt das Teil; dafür reicht der
  angetippte Grund, bei „Sonstiges“ braucht es einen Kommentar.
- Schaden melden: Teil wählen, Grund und Pflichtkommentar, sperren (jede Rolle ab Verleiher).
- Keine neue Fachlogik: `lending.services.return_loans` und `inventory.services.record_event`.
  Nach jedem Vorgang meldet das Vereinsgerät ab.
"""

from dataclasses import dataclass
from typing import Any

from django.contrib import messages
from django.http import HttpRequest, HttpResponse, QueryDict
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from accounts.rights import Right, has_right, requires
from counter import kinds, stock
from counter.damage import DamageError, lock_comment
from counter.forms import DamageForm
from counter.views import _actor, _int, finish
from inventory.models import EventKind, Part
from inventory.services import InventoryError, record_event
from lending import services
from lending.forms import ReturnForm
from lending.models import Booking, Loan, LoanStatus
from lending.receipts import ReceiptKind
from lending.services import LendingError
from lending.views import extension_page, extension_text, send_extension_confirmation


def _entry_context() -> dict[str, Any]:
    return {"prefix": kinds.ei_prefix(), "all_kinds": kinds.all_kinds(), "digits": "1234567890"}


@dataclass(frozen=True)
class OpenBooking:
    booking: Booking
    name: str
    count: int
    due: Any
    late: int  # Tage überfällig; 0 = heute fällig oder später


def _open_bookings() -> list[OpenBooking]:
    today = services.today()
    lent = (
        Loan.objects.filter(status=LoanStatus.LENT)
        .select_related("booking__borrower")
        .order_by("booking_id", "pk")
    )
    grouped: dict[int, list[Loan]] = {}
    for loan in lent:
        grouped.setdefault(loan.booking_id, []).append(loan)
    rows = []
    for loans in grouped.values():
        due = min(loan.end for loan in loans)
        booking = loans[0].booking
        rows.append(
            OpenBooking(
                booking=booking,
                name=booking.borrower.display_name,
                count=len(loans),
                due=due,
                late=max((today - due).days, 0),
            )
        )
    return sorted(rows, key=lambda row: (row.due, row.name))


def _lent_loan(raw: str) -> tuple[Loan | None, str]:
    """Die laufende Ausleihe zu einer gescannten EI, sonst der Grund, warum nicht."""
    component = stock.find(raw)
    if component is None:
        return None, f"{raw.strip() or 'Diese EI'} gibt es nicht."
    loan = (
        Loan.objects.filter(part=component.part, status=LoanStatus.LENT)
        .select_related("booking")
        .first()
    )
    if loan is None:
        return None, f"{component.part.name} {component.ei} ist nicht ausgeliehen."
    return loan, ""


@requires(Right.LEND)
@require_http_methods(["GET", "POST"])
def returns(request: HttpRequest) -> HttpResponse:
    """Rückgabe: offene Ausleihen, überfällige zuerst; ein gescanntes Teil öffnet seine."""
    if request.method == "POST":
        loan, problem = _lent_loan(request.POST.get("ei", ""))
        if loan is None:
            messages.error(request, problem)
            return redirect("counter:returns")
        url = reverse("counter:return_booking", args=[loan.booking_id])
        return redirect(f"{url}?teil={loan.pk}")
    context = {"rows": _open_bookings(), "today": services.today(), **_entry_context()}
    return render(request, "counter/returns.html", context)


def _ids(post: QueryDict, key: str, allowed: set[int]) -> set[int]:
    return {int(v) for v in post.getlist(key) if v.isdigit() and int(v) in allowed}


def _mark(post: QueryDict, lent: list[Loan], back: set[int]) -> str:
    """„Als zurück markieren“: ein gescanntes Teil dieser Ausleihe ankreuzen."""
    if "alle" in post:
        back.update(loan.pk for loan in lent)
        return ""
    raw = post.get("ei", "")
    if raw.strip() in {"", kinds.ei_prefix()}:
        return ""
    loan, problem = _lent_loan(raw)
    if loan is None:
        return problem
    if loan.pk not in {item.pk for item in lent}:
        owner = loan.booking.borrower.display_name
        return f"{loan.part.name} gehört zur Ausleihe von {owner}. Bitte dort zurücknehmen."
    back.add(loan.pk)
    return ""


@requires(Right.LEND)
@require_http_methods(["GET", "POST"])
def return_booking(request: HttpRequest, pk: int) -> HttpResponse:
    """Rückgabe eines Vorgangs: Teile ankreuzen oder scannen, Schaden mit Grund, Kurzprüfung,
    Name und Unterschrift (Parität 17)."""
    booking = get_object_or_404(Booking.objects.select_related("borrower"), pk=pk)
    lent = list(
        Loan.objects.filter(booking=booking, status=LoanStatus.LENT)
        .select_related("part")
        .prefetch_related("part__components")
        .order_by("part__name", "pk")
    )
    if not lent:
        messages.error(request, "In diesem Vorgang ist nichts mehr ausgeliehen.")
        return redirect("counter:returns")
    allowed = {loan.pk for loan in lent}
    post = request.POST
    problem = ""
    if request.method == "POST":
        damaged = _ids(post, "schaden", allowed)
        # Ein beschädigtes Teil liegt auf dem Tresen: „Schaden“ heißt auch „zurück“.
        back = _ids(post, "zurueck", allowed) | damaged
        form = ReturnForm(post) if "zuruecknehmen" in post else None
        damage = DamageForm(post)
    else:
        start = _int(request.GET, "teil")
        back = {start} if start in allowed else set()
        damaged = set()
        form = None
        damage = DamageForm()
    if request.method == "POST" and form is None:
        problem = _mark(post, lent, back)
    signer = booking.borrower.display_name
    if form is not None and form.is_valid() and damage.is_valid():
        try:
            return _take_back(request, booking, lent, back, damaged, form, damage)
        except (LendingError, DamageError) as error:
            problem = str(error)
    shown = form or ReturnForm(
        initial={
            "signer_name": post.get("signer_name", signer),
            "comment": post.get("comment", ""),
            "quick_check": bool(post.get("quick_check")),
            "receipt": bool(post.get("receipt")),
        }
    )
    context = {
        "booking": booking,
        "borrower": signer,
        "lent": lent,
        "back": back,
        "damaged": damaged,
        "today": services.today(),
        "form": shown,
        "damage": damage,
        "problem": problem,
        "may_extend": has_right(request.user, Right.EXTEND),
        **_entry_context(),
    }
    status = 400 if form is not None else 200
    return render(request, "counter/return_booking.html", context, status=status)


def _take_back(
    request: HttpRequest,
    booking: Booking,
    lent: list[Loan],
    back: set[int],
    damaged: set[int],
    form: ReturnForm,
    damage: DamageForm,
) -> HttpResponse:
    chosen = [loan for loan in lent if loan.pk in back]
    if not chosen:
        raise LendingError("Bitte mindestens ein zurückgegebenes Teil ankreuzen oder scannen.")
    lock = damaged & back
    comment = None
    if lock:
        data = damage.cleaned_data
        comment = lock_comment(data["reason"], data["comment"], comment_required=False)
    services.return_loans(
        chosen,
        actor=_actor(request),
        quick_check=True,
        signature=form.cleaned_data["signature"],
        signer_name=form.cleaned_data["signer_name"],
        comment=form.cleaned_data["comment"],
        lock_comment=comment,
        lock=lock,
    )
    count = len(chosen)
    text = f"Zurückgenommen: {count} Teil{'e' if count != 1 else ''}"
    if lock:
        text += f", davon {len(lock)} gesperrt"
    receipt = ReceiptKind.RETURN if form.cleaned_data["receipt"] else None
    return finish(request, text + ".", booking=booking, receipt=receipt)


def _damage_target(raw: str) -> tuple[Part | None, str, str]:
    component = stock.find(raw)
    if component is None:
        return None, "", f"{raw.strip() or 'Diese EI'} gibt es nicht."
    part = component.part
    if part.is_retired():
        return None, "", f"{part.name} {component.ei} ist ausgesondert."
    return part, component.ei, ""


@requires(Right.LOCK)
@require_http_methods(["GET", "POST"])
def damage_report(request: HttpRequest) -> HttpResponse:
    """Schaden melden: Teil wählen (EI oder QR-Etikett), Grund, Pflichtkommentar, sperren."""
    post = request.POST
    part: Part | None = None
    ei = problem = ""
    damage = DamageForm(post if "sperren" in post else None)
    if request.method == "POST":
        if "sperren" in post:
            part = Part.objects.filter(pk=_int(post, "teil") or 0).first()
            ei = post.get("teil_ei", "")
            if part is None:
                problem = "Bitte zuerst das Teil wählen."
            elif damage.is_valid():
                try:
                    data = damage.cleaned_data
                    text = lock_comment(data["reason"], data["comment"], comment_required=True)
                    record_event(part, EventKind.LOCKED, actor=_actor(request), comment=text)
                except (DamageError, InventoryError) as error:
                    problem = str(error)
                else:
                    return finish(request, f"Gesperrt: {part.name} {ei}.")
        else:
            part, ei, problem = _damage_target(post.get("ei", ""))
    context = {
        "part": part,
        "ei": ei,
        "locked": part is not None and part.is_locked(),
        "lent": part is not None
        and Loan.objects.filter(part=part, status=LoanStatus.LENT).exists(),
        "damage": damage,
        "problem": problem,
        **_entry_context(),
    }
    status = 400 if problem and "sperren" in post else 200
    return render(request, "counter/damage.html", context, status=status)


@requires(Right.EXTEND)
@require_http_methods(["GET", "POST"])
def extend_booking(request: HttpRequest, pk: int) -> HttpResponse:
    """Verlängern an der Theke (#129): bei der Rückgabe und unter „Heute“; Regeln wie am PC."""
    booking = get_object_or_404(Booking.objects.select_related("borrower"), pk=pk)
    page = extension_page(request, booking)
    if not page.context["rows"] and not page.created:
        messages.error(request, "In diesem Vorgang ist nichts mehr ausgeliehen.")
        return redirect("counter:returns")
    if not page.created:
        status = 400 if request.method == "POST" else 200
        return render(request, "counter/extend.html", page.context, status=status)
    note = send_extension_confirmation(page.created) if page.confirm else None
    return finish(request, extension_text(page.created), note=note)
