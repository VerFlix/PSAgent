"""Reservierung ausgeben (Parität 15), Rückgabe (17), Detailansicht (19), Stornieren."""

import json
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse

from accounts.models import Role, User
from inventory.models import EventKind
from lending.models import Booking, Borrower, BorrowerContact, Loan, LoanEventKind, LoanStatus
from lending.services import create_borrower, lend, reserve, return_loans
from lending.signatures import svg
from privacy.services import erase_borrower
from tests.helpers import make_inspector
from tests.lending.conftest import DAY, STROKES, TODAY, MakePart, checkout_kwargs, return_kwargs

pytestmark = pytest.mark.django_db


@pytest.fixture
def lender_client(client: Client, lender: User) -> Client:
    client.force_login(lender)
    return client


def loan_ids(booking: Booking, status: LoanStatus | None = None) -> list[str]:
    loans = booking.loans.all() if status is None else booking.loans.filter(status=status)
    return [str(loan.pk) for loan in loans]


def checkout_data(booking: Booking, **extra: Any) -> dict[str, Any]:
    return {
        "ausleihe": loan_ids(booking, LoanStatus.RESERVED),
        "borrower": str(booking.borrower_id),
        "name": "",
        "address": "",
        "email": "",
        "phone": "",
        "contact_confirmed": "on",
        "quick_check": "on",
        "gal_provided": "on",
        "signer_name": "Kim Beispiel",
        "signature": json.dumps(STROKES),
        **extra,
    }


def return_data(booking: Booking, **extra: Any) -> dict[str, Any]:
    return {
        "ausleihe": loan_ids(booking, LoanStatus.LENT),
        "comment": "",
        "quick_check": "on",
        "signer_name": "Kim Beispiel",
        "signature": json.dumps(STROKES),
        "lock_reason": "",
        **extra,
    }


# --- Parität 15: Reservierung in Ausleihe umwandeln ---


def test_parity_15_reservation_is_checked_out(
    lender_client: Client, lender: User, make_part: MakePart, borrower: Borrower
) -> None:
    booking = reserve(borrower, [make_part(), make_part()], TODAY, TODAY + DAY, actor=lender)

    response = lender_client.post(
        reverse("lending:booking_checkout", args=[booking.pk]), checkout_data(booking)
    )

    assert response["Location"] == reverse("lending:booking_detail", args=[booking.pk])
    assert set(booking.loans.values_list("status", flat=True)) == {LoanStatus.LENT}
    assert BorrowerContact.objects.get(borrower=borrower).confirmed_at is not None


def test_parity_15_only_chosen_loans_are_checked_out(
    lender_client: Client, lender: User, make_part: MakePart, borrower: Borrower
) -> None:
    booking = reserve(borrower, [make_part(), make_part()], TODAY, TODAY + DAY, actor=lender)
    first, second = booking.loans.order_by("pk")

    lender_client.post(
        reverse("lending:booking_checkout", args=[booking.pk]),
        checkout_data(booking, ausleihe=[str(first.pk)]),
    )

    first.refresh_from_db()
    second.refresh_from_db()
    assert (first.status, second.status) == (LoanStatus.LENT, LoanStatus.RESERVED)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"contact_confirmed": ""}, "Bitte nachfragen"),
        ({"quick_check": ""}, "Kurzkontrolle"),
        ({"signature": ""}, "unterschreiben"),
        ({"ausleihe": []}, "mindestens ein reserviertes Teil"),
    ],
)
def test_parity_15_same_checks_as_checkout(
    change: dict[str, Any],
    message: str,
    lender_client: Client,
    lender: User,
    make_part: MakePart,
    borrower: Borrower,
) -> None:
    booking = reserve(borrower, [make_part()], TODAY, TODAY + DAY, actor=lender)

    response = lender_client.post(
        reverse("lending:booking_checkout", args=[booking.pk]), checkout_data(booking, **change)
    )

    assert response.status_code == 400
    assert message in response.content.decode()
    assert booking.loans.get().status == LoanStatus.RESERVED


def test_parity_15_part_locked_meanwhile_is_refused(
    lender_client: Client, lender: User, make_part: MakePart, borrower: Borrower
) -> None:
    from inventory.services import record_event

    part = make_part()
    booking = reserve(borrower, [part], TODAY, TODAY + DAY, actor=lender)
    record_event(part, EventKind.LOCKED, actor=None, comment="Naht offen")

    response = lender_client.post(
        reverse("lending:booking_checkout", args=[booking.pk]), checkout_data(booking)
    )

    assert response.status_code == 400
    assert "gesperrt" in response.content.decode()
    assert booking.loans.get().status == LoanStatus.RESERVED
    assert BorrowerContact.objects.get(borrower=borrower).confirmed_at is None  # zurückgerollt


def test_parity_15_loan_of_other_booking_cannot_be_injected(
    lender_client: Client, lender: User, make_part: MakePart, borrower: Borrower
) -> None:
    booking = reserve(borrower, [make_part()], TODAY, TODAY + DAY, actor=lender)
    other = reserve(create_borrower("Noa Probe"), [make_part()], TODAY, TODAY + DAY, actor=lender)

    lender_client.post(
        reverse("lending:booking_checkout", args=[booking.pk]),
        checkout_data(booking, ausleihe=loan_ids(other)),
    )

    assert other.loans.get().status == LoanStatus.RESERVED


def test_parity_15_borrower_of_other_booking_is_refused(
    lender_client: Client, lender: User, make_part: MakePart, borrower: Borrower
) -> None:
    booking = reserve(borrower, [make_part()], TODAY, TODAY + DAY, actor=lender)
    stranger = create_borrower("Noa Probe")

    response = lender_client.post(
        reverse("lending:booking_checkout", args=[booking.pk]),
        checkout_data(booking, borrower=str(stranger.pk), phone="0000 9"),
    )

    assert response.status_code == 400
    assert BorrowerContact.objects.get(borrower=stranger).phone == ""


def test_erased_contact_blocks_checkout(
    lender_client: Client, lender: User, make_part: MakePart, borrower: Borrower
) -> None:
    booking = reserve(borrower, [make_part()], TODAY, TODAY + DAY, actor=lender)
    erase_borrower(borrower, actor=None)

    response = lender_client.get(
        reverse("lending:booking_checkout", args=[booking.pk]), follow=True
    )

    assert "Kontaktdaten dieses Entleihers sind gelöscht" in response.content.decode()


# --- Parität 17: Rückgabe ---


def _lent(make_part: MakePart, borrower: Borrower, lender: User, count: int = 1) -> Booking:
    parts = [make_part() for _ in range(count)]
    return lend(borrower, parts, TODAY, TODAY + DAY, **checkout_kwargs(lender))


def test_parity_17_return_with_comment(
    lender_client: Client, lender: User, make_part: MakePart, borrower: Borrower
) -> None:
    booking = _lent(make_part, borrower, lender)

    lender_client.post(
        reverse("lending:booking_return", args=[booking.pk]),
        return_data(booking, comment="alles gut"),
    )

    loan = booking.loans.get()
    assert loan.status == LoanStatus.RETURNED
    event = loan.events.get(kind=LoanEventKind.RETURNED)
    assert (event.actor, event.comment, event.quick_check) == (lender, "alles gut", True)


def test_parity_17_lock_only_marked_parts(
    lender_client: Client, lender: User, make_part: MakePart, borrower: Borrower
) -> None:
    booking = _lent(make_part, borrower, lender, count=2)
    first, second = booking.loans.order_by("pk")

    response = lender_client.post(
        reverse("lending:booking_return", args=[booking.pk]),
        return_data(booking, sperren=[str(first.pk)], lock_reason="Naht offen"),
        follow=True,
    )

    assert "davon 1 gesperrt" in response.content.decode()
    assert first.part.is_locked() and not second.part.is_locked()
    assert first.part.events.get(kind=EventKind.LOCKED).comment == "Naht offen"


def test_parity_17_lock_needs_reason(
    lender_client: Client, lender: User, make_part: MakePart, borrower: Borrower
) -> None:
    booking = _lent(make_part, borrower, lender)

    response = lender_client.post(
        reverse("lending:booking_return", args=[booking.pk]),
        return_data(booking, sperren=loan_ids(booking)),
    )

    assert response.status_code == 400
    assert "Grund der Sperre" in response.content.decode()
    assert booking.loans.get().status == LoanStatus.LENT


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"quick_check": ""}, "Kurzprüfung"),
        ({"signature": ""}, "unterschreiben"),
        ({"ausleihe": []}, "mindestens ein Teil"),
    ],
)
def test_parity_17_return_requirements(
    change: dict[str, Any],
    message: str,
    lender_client: Client,
    lender: User,
    make_part: MakePart,
    borrower: Borrower,
) -> None:
    booking = _lent(make_part, borrower, lender)

    response = lender_client.post(
        reverse("lending:booking_return", args=[booking.pk]), return_data(booking, **change)
    )

    assert response.status_code == 400
    assert message in response.content.decode()
    assert booking.loans.get().status == LoanStatus.LENT


def test_return_service_locks_all_without_selection(
    make_part: MakePart, borrower: Borrower, lender: User
) -> None:
    """Ohne Auswahl wie bisher: alle zurückgegebenen Teile werden gesperrt."""
    booking = _lent(make_part, borrower, lender, count=2)

    return_loans(list(booking.loans.all()), lock_comment="Sturz", **return_kwargs(lender))

    assert all(loan.part.is_locked() for loan in booking.loans.all())


# --- Stornieren ---


def test_lender_cancels_own_reservation(
    lender_client: Client, lender: User, make_part: MakePart, borrower: Borrower
) -> None:
    booking = reserve(borrower, [make_part()], TODAY, TODAY + DAY, actor=lender)

    lender_client.post(
        reverse("lending:booking_cancel", args=[booking.pk]),
        {"ausleihe": loan_ids(booking), "comment": "abgesagt"},
    )

    assert booking.loans.get().status == LoanStatus.CANCELLED


def test_lender_cannot_cancel_foreign_reservation(
    lender_client: Client, make_part: MakePart, borrower: Borrower
) -> None:
    """Verbotener Fall: fremde Reservierung; kein Formular, und der Dienst lehnt ab."""
    other = make_inspector(role=Role.LENDER, qualified=False)
    booking = reserve(borrower, [make_part()], TODAY, TODAY + DAY, actor=other)

    html = lender_client.get(reverse("lending:booking_detail", args=[booking.pk])).content.decode()
    response = lender_client.post(
        reverse("lending:booking_cancel", args=[booking.pk]), {"ausleihe": loan_ids(booking)}
    )

    assert "Reservierung stornieren" not in html
    assert response.status_code == 403
    assert booking.loans.get().status == LoanStatus.RESERVED


# --- Parität 19: Detailansicht ---


def test_parity_19_detail_shows_both_signatures(
    lender_client: Client, lender: User, make_part: MakePart, borrower: Borrower
) -> None:
    booking = _lent(make_part, borrower, lender)
    return_loans(list(booking.loans.all()), comment="ok", **return_kwargs(lender))

    html = lender_client.get(reverse("lending:booking_detail", args=[booking.pk])).content.decode()

    assert html.count('class="signature-view"') == 2
    assert "ausgegeben" in html and "zurückgenommen" in html
    assert "Kurzkontrolle: ja" in html and "GAL bereitgestellt: ja" in html


def test_parity_19_overdue_is_marked(
    lender_client: Client, lender: User, make_part: MakePart, borrower: Borrower, monkeypatch: Any
) -> None:
    booking = _lent(make_part, borrower, lender)
    monkeypatch.setattr("lending.services.today", lambda: TODAY + 5 * DAY)

    html = lender_client.get(reverse("lending:booking_detail", args=[booking.pk])).content.decode()

    assert "überfällig" in html


def test_parity_19_contacts_only_while_lent(
    lender_client: Client, lender: User, make_part: MakePart, borrower: Borrower
) -> None:
    reserved = reserve(borrower, [make_part()], TODAY, TODAY + DAY, actor=lender)
    lent = _lent(make_part, borrower, lender)

    html_reserved = lender_client.get(
        reverse("lending:booking_detail", args=[reserved.pk])
    ).content.decode()
    html_lent = lender_client.get(
        reverse("lending:booking_detail", args=[lent.pk])
    ).content.decode()

    assert "Teststraße" not in html_reserved
    assert "Teststraße" in html_lent


def test_viewer_cannot_open_booking(
    client: Client, lender: User, make_part: MakePart, borrower: Borrower
) -> None:
    booking = reserve(borrower, [make_part()], TODAY, TODAY + DAY, actor=lender)
    client.force_login(make_inspector(role=Role.VIEWER, qualified=False))

    assert client.get(reverse("lending:booking_detail", args=[booking.pk])).status_code == 403


def test_svg_contains_only_numbers() -> None:
    picture = svg([[[10, 20], [30.5, 40]], [["<script>", 1]], "kaputt", [[1, 2]]])

    assert picture["paths"] == ["M 10 20 L 30.5 40", "M 1 2"]
    assert picture["viewbox"] == "0 0 600 200"


def test_svg_grows_for_large_legacy_drawings() -> None:
    assert svg([[[0, 0], [900, 450]]])["viewbox"] == "0 0 900 450"


def test_loan_count_unchanged_after_failed_actions(
    lender_client: Client, lender: User, make_part: MakePart, borrower: Borrower
) -> None:
    booking = _lent(make_part, borrower, lender)
    before = Loan.objects.count()

    lender_client.post(reverse("lending:booking_checkout", args=[booking.pk]), {})
    lender_client.post(reverse("lending:booking_cancel", args=[booking.pk]), {})

    assert Loan.objects.count() == before
