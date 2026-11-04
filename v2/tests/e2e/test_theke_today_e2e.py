"""Ende-zu-Ende: „Heute“ an der Theke (Tablet 1280 x 800) und Theke-Einstellungen mit YubiKey
(#127, Teil 3)."""

from datetime import timedelta

import pytest
from django.utils import timezone
from playwright.sync_api import Page, expect

from accounts.models import Role
from counter.models import CounterSetting
from lending.services import reserve
from tests.e2e.conftest import add_yubikey, assert_fits_width, sign_in
from tests.e2e.test_theke_e2e import TABLET, choose_period, known_borrower, ready_part
from tests.e2e.test_theke_return_e2e import lent_since

pytestmark = [pytest.mark.e2e, pytest.mark.django_db(transaction=True)]


def test_today_with_pickups_returns_and_stock(page: Page, site: str) -> None:
    page.set_viewport_size(TABLET)
    lender = sign_in(page, site, Role.LENDER)
    ready_part("DAV-HL-G-1", "Klettergurt", size="M")
    ready_part("DAV-HL-G-2", "Klettergurt", size="M-XL")
    lent_since(4, 1, ("DAV-HL-H-31", "Kletterhelm"))
    today = timezone.localdate()
    helmet = ready_part("DAV-HL-H-32", "Kletterhelm")
    reserve(known_borrower(), [helmet], today, today + timedelta(days=2), actor=lender)

    page.goto(f"{site}/theke/")
    page.get_by_role("link", name="Heute").click()
    page.wait_for_load_state("load")

    expect(page.get_by_text("1 Tag überfällig")).to_be_visible()
    expect(page.get_by_text("ab heute")).to_be_visible()
    row = page.get_by_role("row", name="Klettergurt Größe L")
    expect(row.get_by_role("cell").first).to_have_text("1")
    assert_fits_width(page)
    page.get_by_role("button", name="Kim Beispiel").click()
    page.wait_for_load_state("load")
    expect(page.get_by_role("heading", name="Entleiher")).to_be_visible()


def test_admin_sets_helmets_by_size_and_two_weeks(page: Page, site: str) -> None:
    add_yubikey(page, sign_in(page, site, Role.ADMIN))
    ready_part("DAV-HL-H-31", "Kletterhelm", size="M-L")

    page.goto(f"{site}/theke/")
    page.get_by_role("link", name="Theke-Einstellungen").click()
    page.wait_for_load_state("load")
    page.get_by_label("nach Größe H", exact=True).check()
    page.get_by_label("Vorauswahl der Leihdauer").select_option("14")
    page.get_by_role("button", name="Speichern (mit YubiKey)").click()

    expect(page.get_by_text("Theke-Einstellungen gespeichert.")).to_be_visible()
    setting = CounterSetting.objects.get()
    assert setting.default_days == 14
    assert {entry["code"] for entry in setting.kinds if entry["sized"]} == {"G", "H"}
    assert len(setting.kinds) == 6  # unveränderte Zeilen bleiben erhalten

    page.goto(f"{site}/theke/")
    page.get_by_role("button", name="Ausgabe").click()
    expect(page.get_by_role("button", name="2 Wochen")).to_have_attribute("aria-pressed", "true")
    choose_period(page, "2 Wochen")
    expect(page.get_by_role("button", name="Helm Größe M hinzufügen, 1 frei")).to_be_visible()
