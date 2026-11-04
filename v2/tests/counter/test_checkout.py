"""Ausgabe an der Theke in vier Schritten (#127): Dauer → Teile → Entleiher → Unterschrift.

Festlegung (Felix, 31.10. und 03.11.2026): dieselben Rechte und Regeln wie am PC (Parität 13–15),
Pflichten wie am PC (Kurzkontrolle, GAL, Unterschrift), Gurte nach Größe (#124), Abmelden nach
jedem Vorgang am Vereinsgerät. Die Sitzung hält nur Verweise, keine Personendaten.
"""

import json
from typing import Any

import pytest
from django.conf import settings
from django.http import HttpResponse
from django.test import Client
from django.urls import reverse

from accounts.devices import register_device
from accounts.models import Role, User
from counter.cart import SESSION_KEY
from inventory.labels import target_url as label_link
from inventory.models import EventKind, Part
from inventory.services import record_event
from lending.models import Booking, Borrower, BorrowerContact, Loan, LoanEvent, LoanStatus
from lending.services import create_borrower, lend, reserve
from tests.counter.conftest import DAY, STROKES, TODAY, MakePart
from tests.helpers import make_inspector

pytestmark = pytest.mark.django_db

PARTS = reverse("counter:checkout_parts")
BORROWER = reverse("counter:checkout_borrower")
SIGN = reverse("counter:checkout_sign")


def sign_data(**changes: Any) -> dict[str, Any]:
    return {
        "quick_check": "on",
        "gal_provided": "on",
        "signer_name": "Kim Beispiel",
        "signature": json.dumps(STROKES),
        **changes,
    }


def start(client: Client, days: int = 7) -> None:
    client.post(reverse("counter:checkout_new"))
    response = client.post(reverse("counter:checkout_period"), {"tage": days})
    assert response.status_code == 302 and response["Location"] == PARTS


def cart_of(client: Client) -> dict[str, Any]:
    return dict(client.session.get(SESSION_KEY) or {})


def chosen_eis(client: Client) -> list[str]:
    ids = [line["part"] for line in cart_of(client)["lines"]]
    parts = Part.objects.prefetch_related("components").in_bulk(ids)
    return [parts[pk].components.get().ei for pk in ids]


def make_counter_device(client: Client) -> None:
    admin = make_inspector(role=Role.ADMIN, qualified=False)
    response = HttpResponse()
    register_device("Tablet Ausgabe", actor=admin, response=response, counter=True)
    cookie = settings.PSAGENT_CLUB_DEVICE_COOKIE
    client.cookies[cookie] = response.cookies[cookie].value


# --- Start und Rechte ---


def test_home_shows_the_four_tiles(client_lender: Client) -> None:
    page = client_lender.get(reverse("counter:home")).content.decode()

    for label in ("Ausgabe", "Rückgabe", "Schaden melden", "Heute", "Reservieren", "Mein Konto"):
        assert label in page


@pytest.mark.parametrize(
    "name", ["counter:home", "counter:checkout_period", "counter:checkout_sign"]
)
def test_viewer_does_not_get_into_the_counter(client: Client, name: str) -> None:
    client.force_login(make_inspector(role=Role.VIEWER, qualified=False))

    assert client.get(reverse(name)).status_code == 403


def test_counter_device_starts_at_the_counter(client: Client, lender: User) -> None:
    make_counter_device(client)
    client.force_login(lender)

    response = client.get(reverse("home"))

    assert response.status_code == 302 and response["Location"] == reverse("counter:home")


def test_pc_keeps_the_normal_start_page(client_lender: Client) -> None:
    assert client_lender.get(reverse("home")).status_code == 200


# --- Schritt 1: Dauer ---


def test_duration_sets_the_return_date(client_lender: Client) -> None:
    start(client_lender, days=14)

    assert cart_of(client_lender)["end"] == (TODAY + 14 * DAY).isoformat()


def test_one_week_is_preselected(client_lender: Client) -> None:
    page = client_lender.get(reverse("counter:checkout_period")).content.decode()

    assert 'value="7" class="chip on" aria-pressed="true"' in page


def test_custom_date_and_past_date(client_lender: Client) -> None:
    period = reverse("counter:checkout_period")
    client_lender.post(reverse("counter:checkout_new"))

    page = client_lender.post(period, {"datum": (TODAY - DAY).isoformat()}, follow=True)
    assert "in der Vergangenheit" in page.content.decode()
    assert not cart_of(client_lender).get("end")

    client_lender.post(period, {"datum": (TODAY + 10 * DAY).isoformat()})
    assert cart_of(client_lender)["end"] == (TODAY + 10 * DAY).isoformat()


def test_parts_need_a_duration_first(client_lender: Client) -> None:
    client_lender.post(reverse("counter:checkout_new"))

    response = client_lender.get(PARTS)

    assert response["Location"] == reverse("counter:checkout_period")


# --- Schritt 2: Teile ---


def test_belts_by_size_exact_first_then_narrowest(
    client_lender: Client, make_part: MakePart
) -> None:
    """#124: genau passende Gurte vor verstellbaren, der engste Bereich zuerst."""
    make_part("DAV-HL-G-1", size="M-XL")
    make_part("DAV-HL-G-2", size="M")
    make_part("DAV-HL-G-3", size="S-M")
    make_part("DAV-HL-G-4", size="L")
    start(client_lender)

    for _ in range(3):
        client_lender.post(PARTS, {"groesse": "G:M"})
    refused = client_lender.post(PARTS, {"groesse": "G:M"}, follow=True).content.decode()

    assert chosen_eis(client_lender) == ["DAV-HL-G-2", "DAV-HL-G-3", "DAV-HL-G-1"]
    assert "keiner frei" in refused


def test_belt_without_size_and_kids_sizes(client_lender: Client, make_part: MakePart) -> None:
    make_part("DAV-HL-G-1", size="")
    make_part("DAV-HL-G-2", size="K1")
    make_part("DAV-HL-G-3", size="XS-XL")
    start(client_lender)

    client_lender.post(PARTS, {"groesse": "G:"})
    client_lender.post(PARTS, {"groesse": "G:K1"})

    assert chosen_eis(client_lender) == ["DAV-HL-G-1", "DAV-HL-G-2"]


def test_only_parts_free_until_the_return_date_are_offered(
    client_lender: Client, make_part: MakePart, borrower: Borrower, lender: User
) -> None:
    """Fachregel: nicht gesperrt, PSA-Prüfung nach der Rückgabe, keine Überschneidung."""
    make_part("DAV-HL-H-1", designation="Helm", next_due=TODAY + 3 * DAY)
    locked = make_part("DAV-HL-H-2", designation="Helm")
    record_event(locked, EventKind.LOCKED, actor=lender, comment="Riss in der Schale")
    taken = make_part("DAV-HL-H-3", designation="Helm")
    reserve(borrower, [taken], TODAY + DAY, TODAY + 2 * DAY, actor=lender)
    make_part("DAV-HL-H-4", designation="Helm")
    start(client_lender, days=7)

    client_lender.post(PARTS, {"mehr": "H"})
    refused = client_lender.post(PARTS, {"mehr": "H"}, follow=True).content.decode()

    assert chosen_eis(client_lender) == ["DAV-HL-H-4"]
    assert "keins mehr frei" in refused


@pytest.mark.parametrize(
    ("ei", "reason"),
    [
        ("DAV-HL-H-1", "muss vor der nächsten PSA-Prüfung"),
        ("DAV-HL-H-2", "ist gesperrt"),
        ("DAV-HL-H-3", "im Zeitraum bereits vergeben"),
        ("DAV-HL-X-99", "gibt es nicht"),
    ],
)
def test_other_part_by_ei_names_the_reason(
    client_lender: Client,
    make_part: MakePart,
    borrower: Borrower,
    lender: User,
    ei: str,
    reason: str,
) -> None:
    make_part("DAV-HL-H-1", designation="Helm", next_due=TODAY + 3 * DAY)
    locked = make_part("DAV-HL-H-2", designation="Helm")
    record_event(locked, EventKind.LOCKED, actor=lender, comment="Riss in der Schale")
    lend(
        borrower,
        [make_part("DAV-HL-H-3", designation="Helm")],
        TODAY,
        TODAY + 2 * DAY,
        actor=lender,
        quick_check=True,
        gal_provided=True,
        signature=STROKES,
        signer_name="Kim Beispiel",
    )
    start(client_lender)

    page = client_lender.post(PARTS, {"ei": ei}, follow=True).content.decode()

    assert reason in page
    assert cart_of(client_lender)["lines"] == []


def test_other_part_by_ei_or_qr_link(client_lender: Client, make_part: MakePart) -> None:
    make_part("DAV-HL-S-21", designation="Einfachseil 50 m")
    make_part("DAV-HL-EX-207", designation="Expressset")
    start(client_lender)

    client_lender.post(PARTS, {"ei": " dav-hl-s-21 "})
    client_lender.post(PARTS, {"ei": label_link("DAV-HL-EX-207")})
    again = client_lender.post(PARTS, {"ei": "DAV-HL-S-21"}, follow=True).content.decode()

    assert chosen_eis(client_lender) == ["DAV-HL-S-21", "DAV-HL-EX-207"]
    assert "ist schon dabei" in again


def test_swap_cycles_through_free_parts(client_lender: Client, make_part: MakePart) -> None:
    make_part("DAV-HL-KS-8", designation="Klettersteigset")
    make_part("DAV-HL-KS-9", designation="Klettersteigset")
    start(client_lender)

    client_lender.post(PARTS, {"mehr": "KS"})
    client_lender.post(PARTS, {"tauschen": "0"})
    assert chosen_eis(client_lender) == ["DAV-HL-KS-9"]
    client_lender.post(PARTS, {"tauschen": "0"})
    assert chosen_eis(client_lender) == ["DAV-HL-KS-8"]
    client_lender.post(PARTS, {"weniger": "KS"})
    assert chosen_eis(client_lender) == []


def test_swapped_belt_keeps_its_size(client_lender: Client, make_part: MakePart) -> None:
    make_part("DAV-HL-G-1", size="M")
    make_part("DAV-HL-G-2", size="L")
    make_part("DAV-HL-G-3", size="M-L")
    start(client_lender)

    client_lender.post(PARTS, {"groesse": "G:M"})
    client_lender.post(PARTS, {"tauschen": "0"})
    assert chosen_eis(client_lender) == ["DAV-HL-G-3"]
    client_lender.post(PARTS, {"tauschen": "0"})
    assert chosen_eis(client_lender) == ["DAV-HL-G-1"]


def test_htmx_swaps_only_the_step(client_lender: Client, make_part: MakePart) -> None:
    make_part("DAV-HL-H-1", designation="Helm")
    start(client_lender)

    response = client_lender.post(PARTS, {"mehr": "H"}, HTTP_HX_REQUEST="true")

    assert response.status_code == 200
    assert 'id="schritt"' in response.content.decode()


def test_suggestions_name_free_and_unavailable_parts(
    client_lender: Client, make_part: MakePart, lender: User
) -> None:
    make_part("DAV-HL-H-1", designation="Helm")
    locked = make_part("DAV-HL-H-10", designation="Helm")
    record_event(locked, EventKind.LOCKED, actor=lender, comment="Riss")
    make_part("DAV-HL-G-1")
    start(client_lender)

    page = client_lender.get(
        reverse("counter:part_suggestions"), {"ei": "DAV-HL-H-1"}
    ).content.decode()
    empty = client_lender.get(reverse("counter:part_suggestions"), {"ei": "DAV-HL-"})

    assert "DAV-HL-H-1" in page and "DAV-HL-H-10" in page and "DAV-HL-G-1" not in page
    assert "ist gesperrt" in page
    assert "DAV-HL" not in empty.content.decode()


# --- Schritt 3 und 4 ---


def choose_helmet(client: Client, make_part: MakePart) -> Part:
    part = make_part("DAV-HL-H-1", designation="Helm")
    start(client)
    client.post(PARTS, {"mehr": "H"})
    return part


def test_known_person_needs_the_contact_check(
    client_lender: Client, make_part: MakePart, borrower: Borrower
) -> None:
    choose_helmet(client_lender, make_part)

    client_lender.post(BORROWER, {"waehlen": borrower.pk})
    assert client_lender.get(SIGN)["Location"] == BORROWER
    client_lender.post(BORROWER, {"unveraendert": "1"})

    assert client_lender.get(SIGN).status_code == 200


def test_lend_at_the_counter(
    client_lender: Client, make_part: MakePart, borrower: Borrower, lender: User
) -> None:
    part = choose_helmet(client_lender, make_part)
    client_lender.post(BORROWER, {"waehlen": borrower.pk})
    client_lender.post(BORROWER, {"unveraendert": "1"})

    response = client_lender.post(SIGN, sign_data(receipt="on"))

    assert response.status_code == 302 and response["Location"] == reverse("counter:home")
    loan = Loan.objects.get(part=part)
    assert loan.status == LoanStatus.LENT and (loan.start, loan.end) == (TODAY, TODAY + 7 * DAY)
    event = LoanEvent.objects.get(loan=loan)
    assert (event.actor, event.quick_check, event.gal_provided) == (lender, True, True)
    assert SESSION_KEY not in client_lender.session


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"quick_check": ""}, "Kurzkontrolle"),
        ({"gal_provided": ""}, "GAL bereitgestellt"),
        ({"signature": ""}, "unterschreiben"),
    ],
)
def test_same_duties_as_at_the_pc(
    client_lender: Client,
    make_part: MakePart,
    borrower: Borrower,
    changes: dict[str, str],
    message: str,
) -> None:
    choose_helmet(client_lender, make_part)
    client_lender.post(BORROWER, {"waehlen": borrower.pk})
    client_lender.post(BORROWER, {"unveraendert": "1"})

    response = client_lender.post(SIGN, sign_data(**changes))

    assert response.status_code == 400 and message in response.content.decode()
    assert not Booking.objects.exists()


def test_counter_device_logs_out_after_the_checkout(
    client: Client, lender: User, make_part: MakePart, borrower: Borrower
) -> None:
    make_counter_device(client)
    client.force_login(lender)
    choose_helmet(client, make_part)
    client.post(BORROWER, {"waehlen": borrower.pk})
    client.post(BORROWER, {"unveraendert": "1"})

    response = client.post(SIGN, sign_data(), follow=True)

    assert response.redirect_chain[0][0] == reverse("accounts:login")
    assert "_auth_user_id" not in client.session
    assert "Ausgegeben: 1 Teil, Rückgabe bis 22.10.2026. Abgemeldet." in response.content.decode()


def test_new_person_in_step_three(client_lender: Client, make_part: MakePart) -> None:
    choose_helmet(client_lender, make_part)

    client_lender.post(
        BORROWER,
        {
            "anlegen": "1",
            "name": "Mara Muster",
            "address": "Teststraße 2, 00000 Beispielstadt",
            "phone": "0000 000000",
            "email": "",
            "member_number": "999/00/008150*1000*2020*2020*01011990",
        },
    )

    person = Borrower.objects.get(contact__name="Mara Muster")
    assert BorrowerContact.objects.get(borrower=person).member_number == "99900008150"
    assert client_lender.get(SIGN).status_code == 200
    stored = json.dumps(cart_of(client_lender))
    assert "Mara" not in stored and "Teststraße" not in stored and "99900008150" not in stored


def test_new_person_with_gaps_must_complete_them(
    client_lender: Client, make_part: MakePart
) -> None:
    choose_helmet(client_lender, make_part)
    client_lender.post(BORROWER, {"anlegen": "1", "name": "Mara Muster"})
    assert client_lender.get(SIGN)["Location"] == BORROWER

    client_lender.post(
        BORROWER, {"neue_angaben": "1", "address": "Teststraße 2", "email": "mara@example.org"}
    )

    assert client_lender.get(SIGN).status_code == 200


def test_search_by_name_and_member_card(
    client_lender: Client, borrower: Borrower, lender: User
) -> None:
    other = create_borrower("Kim Probe", actor=lender)
    from lending import members

    members.link(other, "99900004711", actor=lender)
    search = reverse("counter:borrower_search")

    by_name = client_lender.get(search, {"suche": "Kim"}).content.decode()
    by_card = client_lender.get(search, {"suche": "999/00/004711"}).content.decode()

    assert "Kim Beispiel" in by_name and "Kim Probe" in by_name
    assert "Kim Probe" in by_card and "Kim Beispiel" not in by_card
    assert "Teststraße" not in by_name  # Verleiher sehen nur den Namen


# --- Reservierung abholen ---


def test_pick_up_a_reservation(
    client_lender: Client, make_part: MakePart, borrower: Borrower, lender: User
) -> None:
    helmet = make_part("DAV-HL-H-1", designation="Helm")
    belt = make_part("DAV-HL-G-1", size="M")
    later = make_part("DAV-HL-G-2", size="L")
    booking = reserve(borrower, [helmet, belt], TODAY, TODAY + 3 * DAY, actor=lender)
    reserve(borrower, [later], TODAY + 5 * DAY, TODAY + 6 * DAY, actor=lender)
    pickups = reverse("counter:checkout_reservations")

    page = client_lender.get(pickups).content.decode()
    assert "Kim Beispiel" in page and "2 Teile" in page and "1 Teil " not in page
    client_lender.post(pickups, {"vorgang": booking.pk})
    assert client_lender.get(PARTS)["Location"] == BORROWER
    client_lender.post(BORROWER, {"unveraendert": "1"})
    reserved = list(booking.loans.order_by("pk"))
    response = client_lender.post(SIGN, sign_data(ausleihe=[reserved[0].pk]))

    assert response.status_code == 302
    statuses = [loan.status for loan in booking.loans.order_by("pk")]
    assert statuses == [LoanStatus.LENT, LoanStatus.RESERVED]


def test_reservation_borrower_cannot_be_swapped(
    client_lender: Client, make_part: MakePart, borrower: Borrower, lender: User
) -> None:
    booking = reserve(
        borrower, [make_part("DAV-HL-H-1", designation="Helm")], TODAY, TODAY, actor=lender
    )
    other = create_borrower("Noa Probe", actor=lender)
    client_lender.post(reverse("counter:checkout_reservations"), {"vorgang": booking.pk})

    client_lender.post(BORROWER, {"waehlen": other.pk})

    assert cart_of(client_lender)["borrower"] == borrower.pk
