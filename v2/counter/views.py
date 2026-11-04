"""Theke (#127): Touch-Oberfläche für die Ausgabe am Tablet.

Festlegung (Felix, 31.10. und 03.11.2026): dieselben Rechte und Regeln wie am PC, keine neue
Fachlogik. Alle Seiten ab Verleiher (Recht „Reservieren, ausgeben, zurücknehmen“); an Theke-
Geräten gelten ohnehin höchstens Verleiher-Rechte (#126). Ausgabe in vier Schritten: Dauer →
Teile → Entleiher → Unterschrift, mit denselben Pflichten wie am PC (Kurzkontrolle, GAL,
Unterschrift; Parität 14 und 15). Nach jedem Vorgang meldet das Vereinsgerät ab.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from django.contrib import messages
from django.db import transaction
from django.http import HttpRequest, HttpResponse, QueryDict
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from accounts.devices import finish_operation
from accounts.models import User
from accounts.rights import Right, has_right, requires
from counter import config, kinds, stock
from counter.cart import Cart, Line, clear, load, save
from counter.forms import ContactForm, PersonForm
from inventory.models import Part
from inventory.sizes import ADULT, KIDS, size_label
from lending import members, receipts, services
from lending.forms import CheckoutForm
from lending.models import Booking, Borrower, BorrowerContact, Loan, LoanStatus
from lending.receipts import ReceiptKind
from lending.services import LendingError

DURATIONS = config.DURATIONS
WANTED = [*ADULT, *KIDS, stock.NO_SIZE]  # Größen-Tasten für Gurte; "" = ohne Größenangabe
MORE = (
    ("Reservieren", "lending:new_booking"),
    ("Person anlegen", "lending:borrower_create"),
    ("Kalender", "lending:calendar"),
    ("Teile", "inventory:part_list"),
    ("Prüfberichte", "inspections:reports"),
    ("Vorgänge", "lending:overview"),
    ("Notbetrieb", "emergency:index"),
    ("Mein Konto", "accounts:account"),
)


@dataclass(frozen=True)
class Notice:
    kind: str  # "error" oder "info"
    text: str


def _actor(request: HttpRequest) -> User:
    assert isinstance(request.user, User)  # noqa: S101 – von requires() geprüft
    return request.user


def _htmx(request: HttpRequest) -> bool:
    return request.headers.get("HX-Request") == "true"


def _tell(request: HttpRequest, notice: Notice | None) -> None:
    if notice is not None:
        level = messages.ERROR if notice.kind == "error" else messages.INFO
        messages.add_message(request, level, notice.text)


def _int(post: QueryDict, key: str) -> int | None:
    value = post.get(key, "")
    return int(value) if value.isdigit() else None


# --- Start ---


@requires(Right.LEND)
@require_GET
def home(request: HttpRequest) -> HttpResponse:
    """Startseite der Theke: vier große Kacheln, darunter die übrigen Bereiche."""
    context = {"more": MORE, "may_configure": has_right(request.user, Right.COUNTER_SETTINGS)}
    return render(request, "counter/home.html", context)


# --- Schritte ---


def _borrower_of(cart: Cart) -> Borrower | None:
    if cart.borrower is None:
        return None
    return Borrower.objects.filter(pk=cart.borrower, contact__isnull=False).first()


def _reserved(cart: Cart) -> list[Loan]:
    if cart.reservation is None:
        return []
    return list(
        Loan.objects.filter(booking_id=cart.reservation, status=LoanStatus.RESERVED)
        .select_related("part", "booking")
        .prefetch_related("part__components", "part__documents")
        .order_by("part__name", "pk")
    )


def _parts_ready(cart: Cart) -> bool:
    return bool(cart.reservation) or (cart.end is not None and bool(cart.lines))


def _borrower_ready(cart: Cart) -> bool:
    borrower = _borrower_of(cart)
    if borrower is None or not cart.confirmed:
        return False
    return not BorrowerContact.objects.get(borrower=borrower).gaps()


def _steps(cart: Cart, current: int) -> list[dict[str, Any]]:
    """Schrittleiste; erreichbar ist ein Schritt, wenn die vorigen erledigt sind."""
    if cart.reservation:
        entries = [(1, "Reservierung", "counter:checkout_reservations", True)]
    else:
        entries = [
            (1, "Dauer", "counter:checkout_period", True),
            (2, "Teile", "counter:checkout_parts", cart.end is not None),
        ]
    entries += [
        (3, "Entleiher", "counter:checkout_borrower", _parts_ready(cart)),
        (4, "Unterschrift", "counter:checkout_sign", _borrower_ready(cart)),
    ]
    return [
        {
            "number": shown,
            "name": name,
            "url": reverse(url) if reachable else "",
            "current": step == current,
        }
        for shown, (step, name, url, reachable) in enumerate(entries, start=1)
    ]


@requires(Right.LEND)
@require_POST
def checkout_new(request: HttpRequest) -> HttpResponse:
    """Neue Ausgabe: verwirft einen angefangenen Vorgang."""
    clear(request)
    return redirect("counter:checkout_period")


def _end_from(post: QueryDict, today: date) -> date | str:
    days = _int(post, "tage")
    if days is not None:
        if days not in {d for d, _ in DURATIONS}:
            return "Bitte eine der Dauern wählen."
        return today + timedelta(days=days)
    try:
        end = date.fromisoformat(post.get("datum", ""))
    except ValueError:
        return "Bitte ein Rückgabedatum wählen."
    if end < today:
        return "Das Rückgabedatum liegt in der Vergangenheit."
    return end


@requires(Right.LEND)
@require_http_methods(["GET", "POST"])
def checkout_period(request: HttpRequest) -> HttpResponse:
    """Schritt 1: Dauer. Danach zeigt die Theke nur Teile, die bis dahin frei sind."""
    cart = load(request)
    if cart.reservation:
        return redirect("counter:checkout_borrower")
    today = services.today()
    if request.method == "POST":
        end = _end_from(request.POST, today)
        if isinstance(end, str):
            messages.error(request, end)
        else:
            cart.end = end
            save(request, cart)
            return redirect("counter:checkout_parts")
    chosen = cart.end or today + timedelta(days=config.default_days())
    options = [
        {"days": days, "label": label, "date": today + timedelta(days=days)}
        for days, label in DURATIONS
    ]
    for option in options:
        option["on"] = option["date"] == chosen
    context = {
        "options": options,
        "today": today,
        "chosen": chosen,
        "custom": not any(option["on"] for option in options),
        "steps": _steps(cart, 1),
    }
    return render(request, "counter/period.html", context)


def _quick(code: str) -> kinds.Kind | None:
    kind = kinds.by_code(code)
    return kind if kind is not None and kind.quick else None


def _wanted_label(wanted: str) -> str:
    return f"Größe {size_label(wanted)}" if wanted else "ohne Größenangabe"


def _change_parts(cart: Cart, end: date, post: QueryDict) -> Notice | None:
    """Eine Änderung in Schritt 2; gibt einen Hinweis zurück, wenn etwas nicht geht."""
    taken = set(cart.part_ids())
    until = f"bis {end:%d.%m.%Y}"

    if "groesse" in post:
        code, _, wanted = post.get("groesse", "").partition(":")
        kind = _quick(code)
        if kind is None or not kind.sized or wanted not in WANTED:
            return Notice("error", "Unbekannte Art oder Größe.")
        match = stock.for_size(stock.offers(kind, end), wanted, taken)
        if not match:
            return Notice("error", f"{kind.label} ({_wanted_label(wanted)}): {until} keiner frei.")
        cart.lines.append(Line(match[0].part.pk, kind.code, wanted))
        return None

    if "mehr" in post or "weniger" in post:
        quick = _quick(post.get("mehr") or post.get("weniger") or "")
        if quick is None or quick.sized:
            return Notice("error", "Unbekannte Art.")
        if "weniger" in post:
            for position in range(len(cart.lines) - 1, -1, -1):
                if cart.lines[position].kind == quick.code:
                    del cart.lines[position]
                    break
            return None
        free = [offer for offer in stock.offers(quick, end) if offer.part.pk not in taken]
        if not free:
            return Notice("error", f"{quick.label}: {until} keins mehr frei.")
        cart.lines.append(Line(free[0].part.pk, quick.code))
        return None

    for action in ("tauschen", "entfernen"):
        if action in post:
            row = _int(post, action)
            if row is None or row >= len(cart.lines):
                return Notice("error", "Diese Zeile gibt es nicht mehr.")
            if action == "entfernen":
                del cart.lines[row]
                return None
            return _swap(cart, end, row, taken)

    if "ei" in post:
        return _add_by_ei(cart, end, post.get("ei", ""), taken)
    return None


def _swap(cart: Cart, end: date, index: int, taken: set[int]) -> Notice | None:
    line = cart.lines[index]
    kind = _quick(line.kind)
    if kind is None:
        return Notice(
            "info", "Von Hand gewählte Teile tauscht man durch Entfernen und neu Eingeben."
        )
    others = taken - {line.part}
    available = stock.offers(kind, end)
    if line.wanted is not None:
        candidates = stock.for_size(available, line.wanted, others)
    else:
        candidates = [offer for offer in available if offer.part.pk not in others]
    ids = [offer.part.pk for offer in candidates]
    if not ids or ids == [line.part]:
        what = (
            f"{kind.label} ({_wanted_label(line.wanted)})"
            if line.wanted is not None
            else kind.label
        )
        return Notice("info", f"Kein anderes Teil frei: {what} bis {end:%d.%m.%Y}.")
    position = ids.index(line.part) if line.part in ids else -1
    line.part = ids[(position + 1) % len(ids)]
    return None


def _add_by_ei(cart: Cart, end: date, raw: str, taken: set[int]) -> Notice | None:
    component = stock.find(raw)
    if component is None:
        shown = raw.strip() or "Diese EI"
        example = f"{kinds.ei_prefix()}H-31"
        return Notice("error", f"{shown} gibt es nicht. Schreibweise z. B. {example}.")
    part = component.part
    if part.pk in taken:
        return Notice("info", f"{part.name} {component.ei} ist schon dabei.")
    reasons = stock.reasons(part, end)
    if reasons:
        return Notice("error", f"{part.name} {component.ei} {', '.join(reasons)}.")
    cart.lines.append(Line(part.pk))
    return None


def _line_rows(cart: Cart, end: date) -> list[dict[str, Any]]:
    parts = Part.objects.prefetch_related("components").in_bulk(cart.part_ids())
    rows = []
    for line in cart.lines:
        part = parts.get(line.part)
        if part is None:
            continue
        kind = _quick(line.kind)
        component = stock._component(part, kind)
        size = component.size if component else ""
        if line.wanted is not None:
            fit = "passt genau" if size == line.wanted else stock.describe_size(size)
            note = f"{_wanted_label(line.wanted)} · Vorschlag, {fit}"
        elif kind is not None:
            note = "Vorschlag – anderes Teil? Tauschen oder scannen"
        else:
            note = "von Hand"
        rows.append(
            {
                "ei": component.ei if component else "",
                "name": part.name,
                "note": note,
                "problems": stock.reasons(part, end),
                "swappable": kind is not None,
            }
        )
    return rows


def _parts_context(cart: Cart, end: date) -> dict[str, Any]:
    taken = set(cart.part_ids())
    cards = []
    for kind in kinds.quick_kinds():
        available = stock.offers(kind, end)
        card: dict[str, Any] = {
            "kind": kind,
            "count": sum(1 for line in cart.lines if line.kind == kind.code),
            "free": sum(1 for offer in available if offer.part.pk not in taken),
        }
        if kind.sized:
            card["sizes"] = [
                {
                    "value": wanted,
                    "label": size_label(wanted) if wanted else "ohne",
                    "aria": _wanted_label(wanted),
                    "free": len(stock.for_size(available, wanted, taken)),
                }
                for wanted in WANTED
            ]
        cards.append(card)
    rows = _line_rows(cart, end)
    return {
        "end": end,
        "cards": cards,
        "lines": rows,
        "can_continue": bool(rows) and not any(row["problems"] for row in rows),
        "prefix": kinds.ei_prefix(),
        "all_kinds": kinds.all_kinds(),
        "digits": "1234567890",
        "steps": _steps(cart, 2),
    }


@requires(Right.LEND)
@require_http_methods(["GET", "POST"])
def checkout_parts(request: HttpRequest) -> HttpResponse:
    """Schritt 2: Teile. Schnellzugriff (Gurt nach Größe, Klettersteigset, Helm), Tauschen,
    „Anderes Teil“ per EI oder Kamera."""
    cart = load(request)
    if cart.reservation:
        return redirect("counter:checkout_borrower")
    if cart.end is None:
        return redirect("counter:checkout_period")
    notice = None
    if request.method == "POST":
        notice = _change_parts(cart, cart.end, request.POST)
        save(request, cart)
        if not _htmx(request):
            _tell(request, notice)
            return redirect("counter:checkout_parts")
    context = {**_parts_context(cart, cart.end), "notice": notice}
    return render(request, "counter/parts.html", context)


@requires(Right.LEND)
@require_GET
def part_suggestions(request: HttpRequest) -> HttpResponse:
    """htmx: passende EIs zur Eingabe. Ausgabe: mit dem Grund, falls ein Teil bis zur Rückgabe
    nicht frei ist; Rückgabe: nur verliehene Teile; Schaden melden: mit dem Zustand."""
    purpose = request.GET.get("fuer", "ausgabe")
    found = stock.suggest(request.GET.get("ei", ""))
    items = []
    if purpose == "rueckgabe":
        lent = {
            loan.part_id: loan
            for loan in Loan.objects.filter(
                status=LoanStatus.LENT, part__in=[c.part for c in found]
            )
        }
        for component in found:
            loan = lent.get(component.part_id)
            note = [f"fällig {loan.end:%d.%m.%Y}"] if loan else []
            items.append(
                {
                    "ei": component.ei,
                    "name": component.part.name,
                    "note": note,
                    "reasons": [] if loan else ["ist nicht ausgeliehen"],
                }
            )
    elif purpose == "schaden":
        for component in found:
            part = component.part
            items.append(
                {
                    "ei": component.ei,
                    "name": part.name,
                    "note": [str(part.status().label)],
                    "reasons": ["ist bereits gesperrt"] if part.is_locked() else [],
                }
            )
    else:
        cart = load(request)
        taken = set(cart.part_ids())
        if cart.end is not None:
            for component in found:
                part = component.part
                reasons = ["ist schon dabei"] if part.pk in taken else stock.reasons(part, cart.end)
                items.append({"ei": component.ei, "name": part.name, "reasons": reasons})
    return render(request, "counter/_suggestions.html", {"items": items})


def _change_borrower(
    request: HttpRequest, cart: Cart, borrower: Borrower | None
) -> tuple[Notice | None, ContactForm | None, PersonForm | None]:
    post = request.POST
    actor = _actor(request)
    if "waehlen" in post and not cart.reservation:
        pk = _int(post, "waehlen")
        chosen = Borrower.objects.filter(pk=pk, contact__isnull=False).first() if pk else None
        if chosen is None:
            return Notice("error", "Diese Person gibt es nicht."), None, None
        cart.borrower, cart.confirmed = chosen.pk, False
    elif "anderer" in post and not cart.reservation:
        cart.borrower, cart.confirmed = None, False
    elif "unveraendert" in post and borrower is not None:
        cart.confirmed = True
    elif "neue_angaben" in post and borrower is not None:
        contact_form = ContactForm(post)
        if not contact_form.is_valid() or not contact_form.given():
            if contact_form.is_valid():
                contact_form.add_error(None, "Bitte mindestens ein Feld ausfüllen.")
            return None, contact_form, None
        services.overwrite_contact(borrower, actor=actor, **contact_form.given())
        cart.confirmed = True
    elif "anlegen" in post and not cart.reservation:
        person_form = PersonForm(post)
        if not person_form.is_valid():
            return None, None, person_form
        data = person_form.cleaned_data
        try:
            created = services.create_borrower(
                data["name"].strip(),
                address=data["address"].strip(),
                email=data["email"].strip(),
                phone=data["phone"].strip(),
                member_number=data["member_number"],
                actor=actor,
            )
        except LendingError as error:
            person_form.add_error(None, str(error))
            return None, None, person_form
        cart.borrower, cart.confirmed = created.pk, True
    return None, None, None


@requires(Right.LEND)
@require_http_methods(["GET", "POST"])
def checkout_borrower(request: HttpRequest) -> HttpResponse:
    """Schritt 3: Entleiher per Mitgliedsausweis, Namenssuche oder neu; Kontaktdaten wie am PC
    bestätigen („seit … unverändert (nachgefragt)“) oder neu eintragen."""
    cart = load(request)
    if not _parts_ready(cart):
        return redirect("counter:checkout_parts" if cart.end else "counter:checkout_period")
    contact_form = person_form = None
    if request.method == "POST":
        notice, contact_form, person_form = _change_borrower(request, cart, _borrower_of(cart))
        save(request, cart)
        if contact_form is None and person_form is None:
            _tell(request, notice)
            return redirect("counter:checkout_borrower")
    borrower = _borrower_of(cart)
    contact = BorrowerContact.objects.get(borrower=borrower) if borrower else None
    context = {
        "borrower": borrower,
        "contact": contact,
        "gaps": contact.gaps() if contact else [],
        "confirmed": cart.confirmed,
        "reservation": cart.reservation,
        "contact_form": contact_form or ContactForm(),
        "person_form": person_form or PersonForm(),
        "can_continue": _borrower_ready(cart),
        "steps": _steps(cart, 3),
    }
    status = 400 if request.method == "POST" else 200
    return render(request, "counter/borrower.html", context, status=status)


def _looks_like_card(query: str) -> bool:
    try:
        members.normalize(query)
    except LendingError:
        return False
    return True


@requires(Right.LEND)
@require_GET
def borrower_search(request: HttpRequest) -> HttpResponse:
    """htmx: Entleiher nach Namen oder Mitgliedsausweis; Verleiher sehen nur den Namen."""
    query = request.GET.get("suche", "")
    actor = _actor(request)
    card = _looks_like_card(query)
    if card:
        holder = members.find(query, actor=actor)
        found = [holder] if holder is not None else []
    else:
        found = services.search_borrowers(query, actor=actor)
    context = {
        "found": found,
        "card": card,
        "too_short": not card and len(query.strip()) < services.SEARCH_MIN_LENGTH,
    }
    return render(request, "counter/_borrower_results.html", context)


def finish(
    request: HttpRequest,
    text: str,
    *,
    booking: Booking | None = None,
    receipt: ReceiptKind | None = None,
) -> HttpResponse:
    """Vorgang abgeschlossen: ggf. Quittung, Meldung, am Vereinsgerät abmelden (#127)."""
    clear(request)
    note = None
    if booking is not None and receipt is not None:
        try:
            note = (messages.SUCCESS, receipts.send(booking, receipt))
        except receipts.ReceiptError as error:
            note = (messages.WARNING, str(error))
    logged_out = finish_operation(request)
    messages.success(request, f"{text} Abgemeldet." if logged_out else text)
    if note is not None:
        messages.add_message(request, *note)
    return redirect("accounts:login" if logged_out else "counter:home")


@requires(Right.LEND)
@require_http_methods(["GET", "POST"])
def checkout_sign(request: HttpRequest) -> HttpResponse:
    """Schritt 4: Kurzkontrolle, GAL, Unterschrift; dann ausgeben (Parität 14 bzw. 15)."""
    cart = load(request)
    if not _parts_ready(cart):
        return redirect("counter:checkout_period")
    if not _borrower_ready(cart):
        return redirect("counter:checkout_borrower")
    borrower = _borrower_of(cart)
    if borrower is None or cart.end is None:
        return redirect("counter:checkout_borrower")
    reserved = _reserved(cart)
    if cart.reservation and not reserved:
        clear(request)
        messages.error(request, "In diesem Vorgang ist nichts mehr reserviert.")
        return redirect("counter:home")
    parts = [loan.part for loan in reserved] or _cart_parts(cart)
    form = CheckoutForm(request.POST or None, initial={"signer_name": borrower.display_name})
    chosen_ids = {loan.pk for loan in reserved}
    if request.method == "POST":
        chosen_ids = {int(v) for v in request.POST.getlist("ausleihe") if v.isdigit()}
    context = {
        "borrower": borrower,
        "end": cart.end,
        "parts": parts,
        "reserved": reserved,
        "chosen": chosen_ids,
        "form": form,
        "steps": _steps(cart, 4),
    }
    if request.method == "GET" or not form.is_valid():
        return render(request, "counter/sign.html", context, status=200 if not form.errors else 400)
    data = form.cleaned_data
    actor = _actor(request)
    checkout = {
        "actor": actor,
        "quick_check": data["quick_check"],
        "gal_provided": data["gal_provided"],
        "signature": data["signature"],
        "signer_name": data["signer_name"],
    }
    try:
        with transaction.atomic():
            services.confirm_contact(borrower, actor=actor)
            if reserved:
                chosen = [loan for loan in reserved if loan.pk in chosen_ids]
                if not chosen:
                    raise LendingError("Bitte mindestens ein reserviertes Teil wählen.")
                services.check_out(chosen, **checkout)
                booking, count, end = reserved[0].booking, len(chosen), cart.end
            else:
                booking = services.lend(borrower, parts, services.today(), cart.end, **checkout)
                count, end = len(parts), cart.end
    except LendingError as error:
        form.add_error(None, str(error))
        return render(request, "counter/sign.html", context, status=400)
    text = f"Ausgegeben: {_parts_text(count)}, Rückgabe bis {end:%d.%m.%Y}."
    receipt = ReceiptKind.CHECKOUT if data["receipt"] else None
    return finish(request, text, booking=booking, receipt=receipt)


def _parts_text(count: int) -> str:
    return f"{count} Teil{'e' if count != 1 else ''}"


def _cart_parts(cart: Cart) -> list[Part]:
    found = Part.objects.prefetch_related("components", "documents").in_bulk(cart.part_ids())
    return [found[pk] for pk in cart.part_ids() if pk in found]


def pickup_rows() -> list[dict[str, Any]]:
    """Vorgänge mit Reservierungen ab heute oder früher (zum Abholen), nach Beginn sortiert."""
    today = services.today()
    pending = Loan.objects.filter(status=LoanStatus.RESERVED, period__startswith__lte=today)
    bookings = []
    for booking in (
        Booking.objects.filter(loans__in=pending)
        .distinct()
        .select_related("borrower")
        .order_by("pk")
    ):
        loans = [loan for loan in booking.loans.all() if loan.status == LoanStatus.RESERVED]
        bookings.append(
            {
                "booking": booking,
                "name": booking.borrower.display_name,
                "count": len(loans),
                "start": min(loan.start for loan in loans),
                "end": max(loan.end for loan in loans),
            }
        )
    bookings.sort(key=lambda row: (row["start"], row["name"]))
    return bookings


@requires(Right.LEND)
@require_http_methods(["GET", "POST"])
def checkout_reservations(request: HttpRequest) -> HttpResponse:
    """„Reservierung abholen“: Vorgänge mit Reservierungen ab heute oder früher; danach
    Entleiher bestätigen und unterschreiben wie bei der Ausgabe."""
    today = services.today()
    pending = Loan.objects.filter(status=LoanStatus.RESERVED, period__startswith__lte=today)
    if request.method == "POST":
        pk = _int(request.POST, "vorgang")
        loans = list(pending.filter(booking_id=pk)) if pk else []
        if not loans:
            messages.error(request, "Diese Reservierung gibt es nicht mehr.")
            return redirect("counter:checkout_reservations")
        booking = loans[0].booking
        cart = Cart(
            end=max(loan.end for loan in loans),
            borrower=booking.borrower_id,
            reservation=booking.pk,
        )
        save(request, cart)
        return redirect("counter:checkout_borrower")
    bookings = pickup_rows()
    context = {"bookings": bookings}
    return render(request, "counter/reservations.html", context)


def is_counter(request: HttpRequest) -> bool:
    """Läuft die Anfrage an einem Theke-Gerät mit Verleih-Recht? (Startseite → Theke)"""
    device = getattr(request, "club_device", None)
    return bool(device is not None and device.counter and has_right(request.user, Right.LEND))
