"""Ende-zu-Ende: Stammdaten im Browser (#13: Teil anlegen, Sperren; Parität 1, 2)."""

from datetime import date

import pytest
from playwright.sync_api import Page, expect

from accounts.models import Role
from inventory.models import EventKind, Part, PartKind
from inventory.services import create_product, create_system, record_event
from tests.e2e.conftest import assert_fits_width, sign_in
from tests.helpers import adopt_inspection

pytestmark = [pytest.mark.e2e, pytest.mark.django_db(transaction=True)]


def test_trainer_creates_product(page: Page, site: str) -> None:
    sign_in(page, site, Role.TRAINER)

    page.goto(f"{site}/teile/")
    page.get_by_role("link", name="Produkt anlegen").click()
    page.get_by_label("Produktbezeichnung").fill("Klettergurt")
    page.get_by_label("Produktname").fill("Testmodell A1")
    page.get_by_label("Einzelidentifikation (EI)").fill("TST-E2E-1")
    page.get_by_label("oder GAL als Link").fill("https://example.org/gal/a1.pdf")
    page.get_by_role("button", name="Anlegen").click()

    expect(page.get_by_role("heading", name="Klettergurt Testmodell A1")).to_be_visible()
    expect(page.get_by_text("neu, nicht freigegeben")).to_be_visible()
    assert Part.objects.get().components.get().ei == "TST-E2E-1"


def test_trainer_creates_system_with_extra_row(page: Page, site: str) -> None:
    """Parität 2: mehr als 3 Bauteile; die vierte Zeile kommt per htmx dazu."""
    sign_in(page, site, Role.TRAINER)

    page.goto(f"{site}/teile/neu/system/")
    page.get_by_label("Name des Systems").fill("Klettersteigset Test")
    page.get_by_role("button", name="Weiteres Bauteil").click()
    expect(page.locator("fieldset.component")).to_have_count(4)
    for index in range(4):
        page.locator(f"#id_bauteile-{index}-ei").fill(f"TST-SYS-{index + 1}")
    page.get_by_role("button", name="Anlegen").click()

    expect(page.get_by_role("heading", name="Klettersteigset Test")).to_be_visible()
    part = Part.objects.get()
    assert part.kind == PartKind.SYSTEM
    assert part.components.count() == 4


def test_lender_locks_on_phone(device_page: Page, site: str) -> None:
    sign_in(device_page, site, Role.LENDER)
    part = create_product({"designation": "Helm", "ei": "TST-HELM"})
    adopt_inspection([part], next_due=date(2030, 1, 1))

    device_page.goto(f"{site}/teile/{part.pk}/")
    device_page.get_by_label("Grund der Sperre").fill("Riss in der Schale")
    device_page.get_by_role("button", name="Sperren").click()

    expect(device_page.get_by_text("gesperrt", exact=True).first).to_be_visible()
    assert_fits_width(device_page)
    assert part.is_locked()


def test_parts_pages_fit_on_devices(device_page: Page, site: str) -> None:
    sign_in(device_page, site, Role.TRAINER)
    product = create_product(
        {
            "designation": "Klettergurt mit sehr langer Bezeichnung",
            "manufacturer": "Beispiel Bergsport",
            "ei": "TST-M1",
            "serial_number": "SN-0000000001",
        }
    )
    system = create_system("Klettersteigset", [{"ei": f"TST-M{i}"} for i in range(2, 6)])
    record_event(product, EventKind.LOCKED, actor=None, comment="Naht offen")

    pages = [
        ("/teile/", "Teile"),
        ("/teile/neu/produkt/", "Produkt anlegen"),
        ("/teile/neu/system/", "System anlegen"),
        (f"/teile/{product.pk}/", product.name),
        (f"/teile/{system.pk}/", system.name),
    ]
    for path, heading in pages:
        device_page.goto(f"{site}{path}")
        expect(device_page.get_by_role("heading", name=heading, exact=True)).to_be_visible()
        assert_fits_width(device_page)
