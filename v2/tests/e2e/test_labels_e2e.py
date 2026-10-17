"""Ende-zu-Ende: QR-Etikett herunterladen und den Link wie nach einem Kamera-Scan öffnen (#14)."""

from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

from accounts.models import Role
from inventory.services import create_product
from tests.e2e.conftest import assert_fits_width, sign_in

pytestmark = [pytest.mark.e2e, pytest.mark.django_db(transaction=True)]


def test_label_download_and_scan_link(device_page: Page, site: str, tmp_path: Path) -> None:
    page = device_page
    create_product({"designation": "Kletterhelm", "ei": "TST-QE1"})
    sign_in(page, site, Role.TRAINER)

    page.goto(f"{site}/q/TST-QE1")
    expect(page.get_by_role("heading", name="Kletterhelm")).to_be_visible()
    assert_fits_width(page)
    with page.expect_download() as download:
        page.get_by_role("link", name="QR-Etikett (PDF)").click()
    path = tmp_path / "etikett.pdf"
    download.value.save_as(path)
    assert path.read_bytes().startswith(b"%PDF-")

    page.goto(f"{site}/q/TST-UNBEKANNT")
    expect(page.get_by_text("Kein Teil mit der EI „TST-UNBEKANNT“")).to_be_visible()
