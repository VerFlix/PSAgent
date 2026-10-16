"""Ende-zu-Ende: Liste „Nächste Prüfungen“ und Zeitleiste auf allen Geräten (Parität 6)."""

from datetime import timedelta

import pytest
from django.utils import timezone
from playwright.sync_api import Page, expect

from accounts.models import Role
from inventory.models import EventKind
from inventory.services import create_product, record_event
from tests.e2e.conftest import assert_fits_width, sign_in
from tests.helpers import adopt_inspection

pytestmark = [pytest.mark.e2e, pytest.mark.django_db(transaction=True)]


def test_next_due_and_timeline_on_devices(device_page: Page, site: str) -> None:
    page = device_page
    sign_in(page, site, Role.VIEWER)
    part = create_product({"designation": "Klettergurt mit langer Bezeichnung", "ei": "TST-ND1"})
    adopt_inspection([part], next_due=timezone.localdate() + timedelta(days=12))
    record_event(part, EventKind.LOCKED, actor=None, comment="Naht an der Beinschlaufe offen")

    page.goto(f"{site}/pruefung/naechste/")
    page.get_by_label("Schnellwahl").select_option(label="in den nächsten 30 Tagen")
    page.get_by_role("button", name="Filtern").click()
    expect(page.get_by_text("in 12 Tagen")).to_be_visible()
    assert_fits_width(page)

    page.get_by_role("link", name="Klettergurt mit langer Bezeichnung").click()
    expect(page.get_by_role("heading", name="Zeitleiste")).to_be_visible()
    expect(page.get_by_text("PSA-Prüfung: in Ordnung")).to_be_visible()
    assert_fits_width(page)
