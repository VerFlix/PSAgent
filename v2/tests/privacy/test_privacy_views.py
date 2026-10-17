"""Oberfläche zu #63: Löschsperre, Vorschau des Löschlaufs, Datenschutzhinweis, Hinweise."""

import json

import pytest
from django.test import Client
from django.urls import reverse

from accounts.models import Role, User
from emergency import binder
from lending.models import Booking, Borrower
from lending.services import lend, return_loans
from privacy import services
from privacy.models import ErasureHold
from privacy.notice import NO_NAMES, retention_notice
from tests.helpers import make_inspector
from tests.lending.conftest import DAY, TODAY, MakePart, checkout_kwargs, return_kwargs

pytestmark = pytest.mark.django_db


@pytest.fixture
def booking(make_part: MakePart, borrower: Borrower, lender: User) -> Booking:
    booking = lend(borrower, [make_part()], TODAY, TODAY + DAY, **checkout_kwargs(lender))
    return_loans(list(booking.loans.all()), **return_kwargs(lender))
    return booking


def login(client: Client, role: Role) -> User:
    user = make_inspector(role=role, qualified=False)
    client.force_login(user)
    return user


# --- Löschsperre ---


def test_ppe_expert_sets_hold_from_booking_detail(client: Client, booking: Booking) -> None:
    login(client, Role.PPE_EXPERT)
    detail = reverse("lending:booking_detail", args=[booking.pk])

    page = client.get(detail).content.decode()
    response = client.post(
        reverse("privacy:hold", args=[booking.borrower_id]),
        {"reason": "Unfall, Anfrage der Versicherung"},
        follow=True,
    )

    assert "Löschsperre setzen" in page
    assert "Löschsperre für Entleiher" in response.content.decode()
    assert services.hold_of(booking.borrower) is not None
    assert "Unfall, Anfrage der Versicherung" in client.get(detail).content.decode()


def test_lender_sees_no_hold_form(client: Client, booking: Booking) -> None:
    login(client, Role.LENDER)

    page = client.get(reverse("lending:booking_detail", args=[booking.pk])).content.decode()

    assert "Löschsperre" not in page


@pytest.mark.parametrize("role", [Role.TRAINER, Role.LENDER, Role.VIEWER])
def test_hold_view_forbidden_below_ppe_expert(role: Role, client: Client, booking: Booking) -> None:
    """Verbotener Fall: Auch direkt abgeschickt setzt niemand unter PSA-Sachkundig eine Sperre."""
    login(client, role)

    response = client.post(
        reverse("privacy:hold", args=[booking.borrower_id]), {"reason": "Schadensfall"}
    )

    assert response.status_code == 403
    assert not ErasureHold.objects.exists()


def test_hold_without_reason_is_refused(client: Client, booking: Booking) -> None:
    login(client, Role.PPE_EXPERT)

    response = client.post(
        reverse("privacy:hold", args=[booking.borrower_id]), {"reason": " "}, follow=True
    )

    assert "Bitte einen Grund angeben." in response.content.decode()
    assert not ErasureHold.objects.exists()


def test_release_on_privacy_page(client: Client, booking: Booking) -> None:
    services.set_hold(booking.borrower, "Schadensfall", actor=None)
    login(client, Role.ADMIN)

    page = client.get(reverse("privacy:index")).content.decode()
    client.post(reverse("privacy:release", args=[booking.borrower_id]), {"reason": "erledigt"})

    assert "Kim Beispiel" in page and "Schadensfall" in page
    assert services.hold_of(booking.borrower) is None


def test_privacy_page_lists_next_run_and_erasures(
    client: Client, booking: Booking, monkeypatch: pytest.MonkeyPatch
) -> None:
    login(client, Role.PPE_EXPERT)
    later = TODAY.replace(year=TODAY.year + 4, month=1, day=1)
    monkeypatch.setattr("privacy.services.today", lambda: later)

    before = client.get(reverse("privacy:index")).content.decode()
    services.run_retention(later)
    after = client.get(reverse("privacy:index")).content.decode()

    assert f"Kim Beispiel (Entleiher #{booking.borrower_id})" in before
    assert f"Entleiher #{booking.borrower_id} · " in after and "Löschfrist" in after
    assert "Kim Beispiel" not in after


def test_home_links_privacy_page_for_ppe_expert(client: Client) -> None:
    login(client, Role.PPE_EXPERT)
    assert "Datenschutz und Löschfristen" in client.get(reverse("home")).content.decode()

    login(client, Role.TRAINER)
    assert "Datenschutz und Löschfristen" not in client.get(reverse("home")).content.decode()


# --- Hinweise ---


def test_checkout_shows_retention_notice(
    client: Client, make_part: MakePart, borrower: Borrower, lender: User
) -> None:
    from lending.services import reserve

    booking = reserve(borrower, [make_part()], TODAY, TODAY + DAY, actor=lender)
    client.force_login(lender)

    page = client.get(reverse("lending:booking_checkout", args=[booking.pk])).content.decode()

    assert "3 Jahre nach Ende des Jahres der letzten Ausleihe" in retention_notice()
    assert retention_notice() in page


def test_return_form_asks_for_no_names(client: Client, make_part: MakePart, lender: User) -> None:
    from lending.services import create_borrower

    borrower = create_borrower("Kim Beispiel", address="x", email="kim@example.org")
    booking = lend(borrower, [make_part()], TODAY, TODAY + DAY, **checkout_kwargs(lender))
    client.force_login(lender)

    page = client.get(reverse("lending:booking_return", args=[booking.pk])).content.decode()

    assert page.count(NO_NAMES) == 2  # Kommentar und Grund der Sperre


def test_binder_carries_retention_notice(monkeypatch: pytest.MonkeyPatch, lender: User) -> None:
    seen: dict[str, str] = {}

    def render(payload: dict[str, object]) -> bytes:
        seen["data"] = json.dumps(payload)
        return b"%PDF-1.4\n%%EOF\n"

    monkeypatch.setattr(binder, "_render", render)

    binder.create_binder(actor=None)

    assert json.loads(seen["data"])["notice"] == retention_notice()
