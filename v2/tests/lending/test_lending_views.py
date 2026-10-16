"""Verleih im Browser: reservieren, direkt ausgeben, Entleiher, Kontaktdaten (Parität 12–16).

Die Rollen-Matrix je URL steht in `tests/test_url_rights.py`; hier die fachlichen Fälle.
"""

import json
from datetime import timedelta
from typing import Any

import pytest
from django.core.exceptions import PermissionDenied
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role, User
from inventory.models import EventKind
from inventory.services import record_event
from lending.models import Booking, Borrower, BorrowerContact, Loan, LoanEventKind, LoanStatus
from lending.services import (
    SIGNATURE_MAX_POINTS,
    LendingError,
    confirm_contact,
    create_borrower,
    lend,
    overwrite_contact,
    reserve,
    search_borrowers,
)
from tests.helpers import make_inspector
from tests.lending.conftest import DAY, NEXT_DUE, STROKES, TODAY, MakePart, checkout_kwargs

pytestmark = pytest.mark.django_db


@pytest.fixture
def lender_client(client: Client, lender: User) -> Client:
    client.force_login(lender)
    return client


def booking_data(
    *parts: Any, start: Any = TODAY, end: Any = TODAY + DAY, **extra: Any
) -> dict[str, Any]:
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "q": "",
        "teil": [str(part.pk) for part in parts],
        "borrower": "",
        "name": "",
        "address": "",
        "email": "",
        "phone": "",
        **extra,
    }


NEW_PERSON = {
    "name": "Robin Exempel",
    "address": "Beispielweg 4, 00000 Teststadt",
    "email": "robin@example.org",
}


def checkout_data(*parts: Any, **extra: Any) -> dict[str, Any]:
    return {
        **booking_data(*parts),
        "quick_check": "on",
        "gal_provided": "on",
        "signer_name": "Robin Exempel",
        "signature": json.dumps(STROKES),
        **extra,
    }


# --- Parität 12/13: Teile wählen, Verfügbarkeit ---


def test_parity_13_unavailable_parts_show_reason(
    lender_client: Client, make_part: MakePart
) -> None:
    free = make_part()
    new = make_part(released=False)
    locked = make_part()
    record_event(locked, EventKind.LOCKED, actor=None, comment="Naht offen")
    due_soon = make_part(next_due=TODAY + DAY)

    html = lender_client.get(
        reverse("lending:new_booking"),
        {"start": TODAY.isoformat(), "end": (TODAY + 3 * DAY).isoformat()},
    ).content.decode()

    assert f'name="teil" value="{free.pk}"' in html
    for part in (new, locked, due_soon):
        assert f'name="teil" value="{part.pk}"' not in html
    assert "ist neu und noch nicht freigegeben" in html
    assert "ist gesperrt" in html
    assert "muss vor der nächsten PSA-Prüfung" in html


def test_parity_12_filter_by_designation(lender_client: Client, make_part: MakePart) -> None:
    helmet = make_part(designation="Helm")
    harness = make_part()

    html = lender_client.get(
        reverse("lending:new_booking"),
        {"start": TODAY.isoformat(), "end": TODAY.isoformat(), "q": "helm"},
    ).content.decode()

    assert f'value="{helmet.pk}"' in html and f'value="{harness.pk}"' not in html


def test_period_in_the_past_is_rejected(lender_client: Client) -> None:
    html = lender_client.get(
        reverse("lending:new_booking"),
        {"start": (TODAY - DAY).isoformat(), "end": TODAY.isoformat()},
    ).content.decode()

    assert "in der Vergangenheit" in html


# --- Parität 12: reservieren ---


def test_parity_12_reserve_several_parts_for_new_borrower(
    lender_client: Client, lender: User, make_part: MakePart
) -> None:
    parts = [make_part(), make_part()]
    start, end = TODAY + 2 * DAY, TODAY + 4 * DAY

    response = lender_client.post(
        reverse("lending:new_booking"),
        booking_data(*parts, start=start, end=end, action="reserve", name="Noa Probe"),
    )

    booking = Booking.objects.get()
    assert response["Location"] == reverse("lending:booking_detail", args=[booking.pk])
    assert booking.created_by == lender
    assert booking.borrower.display_name == "Noa Probe"
    assert {loan.status for loan in booking.loans.all()} == {LoanStatus.RESERVED}
    assert {(loan.start, loan.end) for loan in booking.loans.all()} == {(start, end)}


def test_reserve_needs_a_part(lender_client: Client) -> None:
    response = lender_client.post(
        reverse("lending:new_booking"), booking_data(action="reserve", name="Noa Probe")
    )

    assert response.status_code == 400
    assert "mindestens ein Teil" in response.content.decode()
    assert not Borrower.objects.exists()


def test_reserve_needs_borrower(lender_client: Client, make_part: MakePart) -> None:
    response = lender_client.post(
        reverse("lending:new_booking"), booking_data(make_part(), action="reserve")
    )

    assert response.status_code == 400
    assert "Entleiher wählen oder einen Namen" in response.content.decode()


def test_parity_13_double_booking_is_rejected_and_rolled_back(
    lender_client: Client, make_part: MakePart, borrower: Borrower, lender: User
) -> None:
    part = make_part()
    reserve(borrower, [part], TODAY, TODAY + DAY, actor=lender)

    response = lender_client.post(
        reverse("lending:new_booking"),
        booking_data(part, action="reserve", name="Noa Probe"),
    )

    assert response.status_code == 400
    assert "bereits vergeben" in response.content.decode()
    assert Borrower.objects.count() == 1  # der neue Entleiher ist zurückgerollt


# --- Parität 14: direkt ausgeben ---


def test_parity_14_lend_goes_through_checkout_page(
    lender_client: Client, make_part: MakePart
) -> None:
    part = make_part()

    page = lender_client.post(
        reverse("lending:new_booking"), booking_data(part, action="lend", **NEW_PERSON)
    )

    html = page.content.decode()
    assert page.status_code == 200
    assert "Kurzkontrolle durchgeführt" in html and "canvas" in html
    assert "GAL Testmodell" in html
    assert not Booking.objects.exists()


def test_parity_14_checkout_lends_with_signature(
    lender_client: Client, lender: User, make_part: MakePart
) -> None:
    part = make_part()

    response = lender_client.post(
        reverse("lending:checkout_new"), checkout_data(part, **NEW_PERSON)
    )

    loan = Loan.objects.get()
    assert response["Location"] == reverse("lending:booking_detail", args=[loan.booking_id])
    assert loan.status == LoanStatus.LENT
    event = loan.events.get(kind=LoanEventKind.CHECKED_OUT)
    assert (event.actor, event.quick_check, event.gal_provided) == (lender, True, True)
    assert event.signature is not None and event.signature.strokes == STROKES
    assert BorrowerContact.objects.get().email == "robin@example.org"


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"quick_check": ""}, "Kurzkontrolle"),
        ({"gal_provided": ""}, "GAL bereitgestellt"),
        ({"signature": ""}, "unterschreiben"),
        ({"signature": "kein json"}, "unlesbar"),
        ({"signature": json.dumps([[[1, 2, 3]]])}, "ungültig"),
    ],
)
def test_parity_14_checkout_requirements(
    change: dict[str, str], message: str, lender_client: Client, make_part: MakePart
) -> None:
    part = make_part()

    response = lender_client.post(
        reverse("lending:checkout_new"), checkout_data(part, **NEW_PERSON, **change)
    )

    assert response.status_code == 400
    assert message in response.content.decode()
    assert not Loan.objects.exists()
    assert not Borrower.objects.exists()


def test_parity_14_lend_only_from_today(lender_client: Client, make_part: MakePart) -> None:
    response = lender_client.post(
        reverse("lending:new_booking"),
        booking_data(
            make_part(), start=TODAY + DAY, end=TODAY + 2 * DAY, action="lend", **NEW_PERSON
        ),
    )

    assert response.status_code == 400
    assert "nur ab heute" in response.content.decode()


def test_parity_14_missing_contact_shown_before_checkout(
    lender_client: Client, make_part: MakePart
) -> None:
    response = lender_client.post(
        reverse("lending:new_booking"),
        booking_data(make_part(), action="lend", name="Noa Probe"),
    )

    assert response.status_code == 400
    assert "Für die Ausgabe fehlt: Adresse, E-Mail oder Telefon" in response.content.decode()


def test_club_device_logs_out_after_lending(
    client: Client, make_part: MakePart, make_user_admin: User
) -> None:
    client.force_login(make_user_admin)
    client.post(reverse("accounts:club_device_register"), {"name": "Tablet"})
    client.post(reverse("accounts:logout"))  # client.logout() löschte auch das Geräte-Cookie
    client.force_login(make_inspector(role=Role.LENDER, qualified=False))

    response = client.post(
        reverse("lending:checkout_new"), checkout_data(make_part(), **NEW_PERSON)
    )

    assert response["Location"] == reverse("accounts:login")
    assert "_auth_user_id" not in client.session
    assert Loan.objects.get().status == LoanStatus.LENT


@pytest.fixture
def make_user_admin(db: None) -> User:
    return make_inspector(role=Role.ADMIN)


# --- Entleiher wiederverwenden, Kontaktdaten (Parität 14, 16) ---


def test_parity_14_known_borrower_needs_confirmation_or_new_data(
    lender_client: Client, make_part: MakePart, borrower: Borrower
) -> None:
    """Ohne Häkchen „unverändert“ und ohne neue Angaben keine Ausgabe an bekannte Entleiher."""
    response = lender_client.post(
        reverse("lending:checkout_new"), checkout_data(make_part(), borrower=str(borrower.pk))
    )

    assert response.status_code == 400
    assert "Bitte nachfragen, ob sich die Kontaktdaten seit" in response.content.decode()
    assert not Loan.objects.exists()


def test_parity_14_confirmed_contact_is_dated(
    lender_client: Client, make_part: MakePart, borrower: Borrower
) -> None:
    lender_client.post(
        reverse("lending:checkout_new"),
        checkout_data(make_part(), borrower=str(borrower.pk), contact_confirmed="True"),
    )

    contact = BorrowerContact.objects.get(borrower=borrower)
    assert contact.confirmed_at is not None
    assert Loan.objects.get().booking.borrower == borrower


def test_parity_16_overwrite_without_seeing(
    lender_client: Client, make_part: MakePart, borrower: Borrower
) -> None:
    """Neue Telefonnummer eintragen; leere Felder lassen Adresse und E-Mail unverändert."""
    lender_client.post(
        reverse("lending:checkout_new"),
        checkout_data(make_part(), borrower=str(borrower.pk), phone="0000 777777"),
    )

    contact = BorrowerContact.objects.get(borrower=borrower)
    assert contact.phone == "0000 777777"
    assert contact.email == "kim.beispiel@example.org"
    assert contact.address == "Teststraße 1, 00000 Beispielstadt"


def test_status_shows_gaps_and_date_but_not_values(
    lender_client: Client, borrower: Borrower
) -> None:
    """Verleiher ohne laufende Ausleihe sehen nur, was fehlt, und seit wann es gilt."""
    BorrowerContact.objects.filter(borrower=borrower).update(email="", phone="")

    html = lender_client.get(
        reverse("lending:borrower_status", args=[borrower.pk])
    ).content.decode()

    assert "Kim Beispiel" in html
    assert "Für die Ausgabe fehlt: E-Mail oder Telefon" in html
    assert timezone.localdate().strftime("%d.%m.%Y") in html
    assert "Teststraße" not in html


def test_status_shows_values_to_experts(client: Client, borrower: Borrower) -> None:
    client.force_login(make_inspector())

    html = client.get(reverse("lending:borrower_status", args=[borrower.pk])).content.decode()

    assert "Teststraße" in html


def test_search_shows_only_names(lender_client: Client, borrower: Borrower) -> None:
    html = lender_client.get(reverse("lending:borrower_search"), {"suche": "kim"}).content.decode()

    assert "Kim Beispiel" in html
    assert "example.org" not in html and "Teststraße" not in html


@pytest.mark.parametrize("query", ["", "k", "ki", "  ki  "])
def test_search_needs_three_characters(
    query: str, lender_client: Client, borrower: Borrower
) -> None:
    html = lender_client.get(reverse("lending:borrower_search"), {"suche": query}).content.decode()

    assert "Mindestens 3 Zeichen" in html and "Kim Beispiel" not in html


def test_search_returns_at_most_ten(lender: User) -> None:
    for number in range(12):
        create_borrower(f"Testperson Muster {number:02d}")

    found = search_borrowers("muster", actor=lender)

    assert len(found) == 10


def test_viewer_cannot_search_or_overwrite(borrower: Borrower) -> None:
    viewer = make_inspector(role=Role.VIEWER, qualified=False)

    with pytest.raises(PermissionDenied):
        search_borrowers("kim", actor=viewer)
    with pytest.raises(PermissionDenied):
        overwrite_contact(borrower, actor=viewer, phone="0000 1")
    with pytest.raises(PermissionDenied):
        confirm_contact(borrower, actor=viewer)
    assert BorrowerContact.objects.get(borrower=borrower).confirmed_at is None


def test_overwrite_ignores_empty_and_marks_confirmed(borrower: Borrower, lender: User) -> None:
    before = BorrowerContact.objects.get(borrower=borrower).updated_at

    contact = overwrite_contact(borrower, actor=lender, address="", email=" ", phone="0000 2")

    assert (contact.phone, contact.email) == ("0000 2", "kim.beispiel@example.org")
    assert contact.updated_at > before
    assert contact.confirmed_at is not None


def test_confirm_keeps_values_and_updated_at(borrower: Borrower, lender: User) -> None:
    before = BorrowerContact.objects.get(borrower=borrower)
    later = timezone.now() + timedelta(seconds=1)

    contact = confirm_contact(borrower, actor=lender)

    assert contact.updated_at == before.updated_at
    assert contact.confirmed_at is not None and contact.checked_at <= later


# --- Unterschrift: Grenzen ---


@pytest.mark.parametrize(
    "strokes",
    [
        [[[0, 0]]] * 101,
        [[[1, 1]] * (SIGNATURE_MAX_POINTS + 1)],
        [[[1, 20000]]],
        [[[-1, 5]]],
        [[[True, 5]]],
        [[]],
    ],
)
def test_signature_limits(
    strokes: Any, make_part: MakePart, borrower: Borrower, lender: User
) -> None:
    with pytest.raises(LendingError, match="Unterschrift"):
        lend(
            borrower,
            [make_part()],
            TODAY,
            TODAY + DAY,
            **{**checkout_kwargs(lender), "signature": strokes},
        )

    assert not Loan.objects.exists()


def test_booking_detail_lists_loans(
    lender_client: Client, make_part: MakePart, borrower: Borrower, lender: User
) -> None:
    part = make_part(designation="Helm")
    booking = reserve(borrower, [part], TODAY, TODAY + DAY, actor=lender)

    html = lender_client.get(reverse("lending:booking_detail", args=[booking.pk])).content.decode()

    assert "Kim Beispiel" in html and part.name in html and "reserviert" in html


def test_next_due_constant_is_far_enough() -> None:
    assert NEXT_DUE > TODAY + 10 * DAY
