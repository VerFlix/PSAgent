"""Ende-zu-Ende: Notfallmappe erzeugen und herunterladen, auf allen Geräten (Parität 22)."""

from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import Page, expect

from accounts.models import Role
from inventory.services import create_product
from tests.e2e.conftest import assert_fits_width, sign_in

pytestmark = [pytest.mark.e2e, pytest.mark.django_db(transaction=True)]


def test_lender_downloads_binder(
    device_page: Page, site: str, tmp_path: Path, settings: Any
) -> None:
    settings.MEDIA_ROOT = tmp_path / "media"
    page = device_page
    sign_in(page, site, Role.LENDER)
    create_product({"designation": "Klettergurt", "ei": "TST-NB1"})

    page.goto(f"{site}/")
    page.get_by_role("link", name="Notbetrieb und Notfallmappe").click()
    expect(page.get_by_text("Noch keine Notfallmappe erzeugt.")).to_be_visible()
    assert_fits_width(page)

    with page.expect_download() as download:
        page.get_by_role("button", name="Erzeugen und herunterladen").click()
    assert download.value.suggested_filename.startswith("notfallmappe-")
    path = tmp_path / "mappe.pdf"
    download.value.save_as(path)
    assert path.read_bytes().startswith(b"%PDF-")

    page.reload()
    expect(page.get_by_text("A-00001 bis A-00010")).to_be_visible()
    with page.expect_download() as again:
        page.get_by_role("link", name="Neueste Notfallmappe herunterladen (PDF)").click()
    again.value.save_as(tmp_path / "neueste.pdf")
    assert (tmp_path / "neueste.pdf").read_bytes() == path.read_bytes()
