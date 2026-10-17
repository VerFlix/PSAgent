"""Ende-zu-Ende: Löschsperre setzen und aufheben (#63), auf Desktop, Handy und Tablet."""

from datetime import timedelta
from typing import Any

import pytest
from django.utils import timezone
from playwright.sync_api import Page, expect

from accounts.models import Role
from lending.services import create_borrower, lend, return_loans
from privacy import services
from tests.e2e.conftest import assert_fits_width, sign_in
from tests.e2e.test_lending_e2e import ready_part

pytestmark = [pytest.mark.e2e, pytest.mark.django_db(transaction=True)]
SIGNED: dict[str, Any] = {"signature": [[[1, 1], [5, 5]]], "signer_name": "Kim Beispiel"}


def test_hold_from_booking_to_privacy_page(device_page: Page, site: str) -> None:
    page = device_page
    expert = sign_in(page, site, Role.PPE_EXPERT)
    borrower = create_borrower("Kim Beispiel", address="Teststraße 1", email="kim@example.org")
    today = timezone.localdate()
    booking = lend(
        borrower,
        [ready_part("TST-D1")],
        today,
        today + timedelta(days=1),
        actor=expert,
        quick_check=True,
        gal_provided=True,
        **SIGNED,
    )
    return_loans(list(booking.loans.all()), actor=expert, quick_check=True, **SIGNED)

    page.goto(f"{site}/verleih/{booking.pk}/")
    page.get_by_label("Grund").fill("Unfall am Klettersteig, Anfrage der Versicherung")
    page.get_by_role("button", name="Löschsperre setzen").click()

    expect(page.get_by_text(f"Löschsperre für Entleiher #{borrower.pk} gesetzt.")).to_be_visible()
    expect(page.get_by_text("Unfall am Klettersteig, Anfrage der Versicherung")).to_be_visible()
    assert_fits_width(page)

    page.get_by_label("Grund für das Aufheben").fill("Versicherung hat abgeschlossen")
    page.get_by_role("button", name="Löschsperre aufheben").click()

    expect(page.get_by_text("Keine Löschsperren.")).to_be_visible()
    assert services.hold_of(borrower) is None
