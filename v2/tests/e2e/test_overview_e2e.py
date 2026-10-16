"""Ende-zu-Ende: Übersicht und Kalender auf Desktop, Handy und Tablet (Parität 18, 20)."""

from datetime import timedelta

import pytest
from django.utils import timezone
from playwright.sync_api import Page, expect

from accounts.models import Role
from lending.services import create_borrower, lend
from tests.e2e.conftest import assert_fits_width, sign_in
from tests.e2e.test_lending_e2e import ready_part

pytestmark = [pytest.mark.e2e, pytest.mark.django_db(transaction=True)]


def test_overview_and_calendar_fit_on_devices(device_page: Page, site: str) -> None:
    page = device_page
    lender = sign_in(page, site, Role.LENDER)
    part = ready_part("TST-K1", designation="Klettergurt mit langer Bezeichnung")
    borrower = create_borrower("Kim Beispiel", address="Teststraße 1", email="kim@example.org")
    today = timezone.localdate()
    lend(
        borrower,
        [part],
        today,
        today + timedelta(days=2),
        actor=lender,
        quick_check=True,
        gal_provided=True,
        signature=[[[1, 1], [5, 5]]],
        signer_name="Kim Beispiel",
    )

    page.goto(f"{site}/verleih/")
    expect(page.get_by_role("heading", name="Ausgeliehen (1)")).to_be_visible()
    assert_fits_width(page)

    page.get_by_role("link", name="Kalender").first.click()
    expect(page.get_by_role("heading", name="Kalender", exact=True)).to_be_visible()
    assert_fits_width(page)
    page.get_by_label(f"{today:%d.%m.%Y}: 1 belegt").click()
    expect(page.get_by_text("Kim Beispiel")).to_be_visible()

    page.get_by_role("link", name="Wochenansicht").click()
    expect(
        page.get_by_text("Klettergurt mit langer Bezeichnung · Kim Beispiel").first
    ).to_be_visible()
    assert_fits_width(page)
