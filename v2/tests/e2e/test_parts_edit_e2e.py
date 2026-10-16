"""Ende-zu-Ende: Teile bearbeiten, GAL an mehrere Teile, GAL im Browser (Parität 3, 4, 21)."""

from pathlib import Path
from typing import Any

import pytest
from django.test import override_settings
from playwright.sync_api import Page, expect

from accounts.models import Role
from inventory.models import Document
from inventory.services import create_product, store_document_file
from tests.e2e.conftest import assert_fits_width, sign_in

pytestmark = [pytest.mark.e2e, pytest.mark.django_db(transaction=True)]
PDF = b"%PDF-1.4 kuenstliche GAL\n%%EOF\n"


@pytest.fixture(autouse=True)
def media_root(tmp_path: Path) -> Any:
    """Dateien landen im Testordner, nie im Repo."""
    with override_settings(MEDIA_ROOT=tmp_path / "media"):
        yield


def test_edit_with_confirmation(page: Page, site: str) -> None:
    """Parität 3: Änderung erst nach Bestätigung; PSA-Felder ohne Sachkunde gesperrt."""
    sign_in(page, site, Role.TRAINER)
    part = create_product(
        {"designation": "Helm", "manufacturer": "Beispiel Bergsport", "ei": "TST-E1"}
    )

    page.goto(f"{site}/teile/{part.pk}/")
    page.get_by_role("link", name="Bearbeiten").click()
    expect(page.get_by_label("Einzelidentifikation (EI) *")).to_be_disabled()
    page.get_by_label("Hersteller").fill("Beispiel Alpin")
    page.get_by_role("button", name="Weiter zur Bestätigung").click()

    expect(page.get_by_role("cell", name="Beispiel Alpin")).to_be_visible()
    page.get_by_role("button", name="Speichern").click()
    expect(page.get_by_text("Änderungen an Helm gespeichert.")).to_be_visible()
    assert part.components.get().manufacturer == "Beispiel Alpin"


def test_gal_for_marked_parts(page: Page, site: str, tmp_path: Path) -> None:
    """Parität 4: Teile in der Liste markieren, eine PDF für alle, gespeichert nur einmal."""
    sign_in(page, site, Role.TRAINER)
    parts = [create_product({"designation": "Gurt", "ei": f"TST-E{i}"}) for i in (10, 11, 12)]
    pdf = tmp_path / "gal.pdf"
    pdf.write_bytes(PDF)

    page.goto(f"{site}/teile/")
    page.get_by_label("Gurt markieren").nth(0).check()
    page.get_by_label("Gurt markieren").nth(2).check()
    page.get_by_role("button", name="GAL an markierte Teile").click()
    expect(page.get_by_text("Markiert: 2 Teil(e)")).to_be_visible()
    page.get_by_label("GAL als PDF").set_input_files(str(pdf))
    page.get_by_label("Titel der GAL").fill("GAL Gurt")
    page.get_by_role("button", name="GAL anhängen").click()

    expect(page.get_by_text("an 2 Teil(e) angehängt")).to_be_visible()
    assert Document.objects.count() == 1
    assert [part.documents.count() for part in parts] == [1, 0, 1]


def test_gal_opens_in_browser(page: Page, site: str) -> None:
    """Parität 21: Die GAL kommt als PDF zum Anzeigen, nicht als Download."""
    sign_in(page, site, Role.VIEWER)
    part = create_product({"designation": "Helm", "ei": "TST-E20"})
    part.documents.add(store_document_file(PDF, "gal.pdf", title="GAL Helm"))

    page.goto(f"{site}/teile/{part.pk}/")
    link = page.get_by_role("link", name="GAL Helm (PDF)")
    response = page.request.get(f"{site}{link.get_attribute('href')}")

    assert response.status == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"].startswith("inline")
    assert response.body() == PDF


def test_edit_and_gal_pages_fit_on_devices(device_page: Page, site: str) -> None:
    sign_in(device_page, site, Role.TRAINER)
    part = create_product(
        {
            "designation": "Klettergurt mit langer Bezeichnung",
            "ei": "TST-E30",
            "serial_number": "SN-1",
        }
    )

    for path in (f"/teile/{part.pk}/bearbeiten/", f"/teile/gal-zuweisen/?teil={part.pk}"):
        device_page.goto(f"{site}{path}")
        expect(device_page.get_by_role("heading").first).to_be_visible()
        assert_fits_width(device_page)
