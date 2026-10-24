"""Ende-zu-Ende: Archiv ausgesonderter Teile (Parität 10, #40) auf Desktop, Handy und Tablet."""

import pytest
from playwright.sync_api import Page, expect

from accounts.models import Role
from inventory.models import EventKind
from inventory.services import create_product, record_event
from tests.e2e.conftest import assert_fits_width, sign_in
from tests.helpers import make_inspector

pytestmark = [pytest.mark.e2e, pytest.mark.django_db(transaction=True)]


def test_parity_10_archive_on_devices(device_page: Page, site: str) -> None:
    page = device_page
    part = create_product({"designation": "Bandschlinge", "ei": "TST-ARCHIV-E2E-1"})
    record_event(part, EventKind.RETIRED, actor=make_inspector(), comment="Naht aufgescheuert")
    create_product({"designation": "Karabiner", "ei": "TST-AKTIV-E2E-1"})
    sign_in(page, site, Role.VIEWER)

    page.goto(f"{site}/teile/")
    expect(page.get_by_text("TST-ARCHIV-E2E-1")).to_have_count(0)
    page.get_by_role("link", name="Archiv ausgesonderter Teile").click()

    expect(page.get_by_role("heading", name="Archiv ausgesonderter Teile")).to_be_visible()
    expect(page.get_by_role("cell", name="Naht aufgescheuert")).to_be_visible()
    expect(page.get_by_text("TST-AKTIV-E2E-1")).to_have_count(0)
    assert_fits_width(page)

    page.get_by_role("link", name=part.name).click()
    expect(page.get_by_text("Naht aufgescheuert")).to_be_visible()
    assert_fits_width(page)
