"""Ende-zu-Ende: Sperrformular nacherfassen und Klärungsliste, auf allen Geräten (Parität 22)."""

from pathlib import Path
from typing import Any

import pytest
from django.utils import timezone
from playwright.sync_api import Page, expect

from accounts.models import Role
from emergency.binder import create_binder
from inventory.services import create_product
from tests.e2e.conftest import assert_fits_width, sign_in

pytestmark = [pytest.mark.e2e, pytest.mark.django_db(transaction=True)]
SCAN = b"%PDF-1.4\n% Scan Sperrformular, kuenstliche Testdaten\n%%EOF\n"


def fill_lock(page: Page, number: str, ei: str, scan: Path) -> None:
    page.get_by_label("Formularnummer (z. B. S-00003)").fill(number)
    page.get_by_label("Scan als PDF oder Fotos (JPEG, PNG)").set_input_files(str(scan))
    page.locator("#id_rows-0-ei").fill(ei)
    page.locator("#id_rows-0-reason").fill("Naht an der Beinschlaufe offen")
    page.locator("#id_rows-0-locked_on").fill(timezone.localdate().isoformat())
    page.locator("#id_rows-0-locked_by_name").fill("Testperson Sperrer")
    page.get_by_role("button", name="Nacherfassen").click()


def test_lock_backfill_and_clarification(
    device_page: Page, site: str, tmp_path: Path, settings: Any
) -> None:
    settings.MEDIA_ROOT = tmp_path / "media"
    page = device_page
    first = create_product({"designation": "Klettergurt", "ei": "TST-NE1"})
    create_product({"designation": "Helm", "ei": "TST-NE2"})
    create_binder(actor=None)
    scan = tmp_path / "scan.pdf"
    scan.write_bytes(SCAN)

    sign_in(page, site, Role.LENDER)
    page.goto(f"{site}/notbetrieb/")
    page.get_by_role("link", name="Sperrungen nacherfassen").click()
    assert_fits_width(page)
    fill_lock(page, "S-00001", "TST-NE1", scan)
    expect(page.get_by_text("Formular S-00001 nacherfasst.")).to_be_visible()
    fill_lock(page, "S-00003", "TST-NE2", scan)
    expect(page.get_by_text("Formular S-00003 nacherfasst.")).to_be_visible()
    first.refresh_from_db()
    assert first.is_locked()

    page.context.clear_cookies()
    sign_in(page, site, Role.PPE_EXPERT, username="e2e-pruefer")
    page.goto(f"{site}/notbetrieb/klaerung/")
    expect(page.get_by_text("Formular S-00002 fehlt")).to_be_visible()
    assert_fits_width(page)
    page.get_by_label("Klärung").fill("Blatt verschrieben, liegt im Fach")
    page.get_by_role("button", name="Als geklärt vermerken").click()
    expect(page.get_by_text("Keine offenen Fälle.")).to_be_visible()
