"""Ende-zu-Ende: Export der Teileliste und vollständige Sicherung herunterladen (Parität 11)."""

import zipfile
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

from accounts.models import Role
from inventory.services import create_product
from tests.e2e.conftest import sign_in

pytestmark = [pytest.mark.e2e, pytest.mark.django_db(transaction=True)]


def test_export_marked_and_all(page: Page, site: str, tmp_path: Path) -> None:
    sign_in(page, site, Role.VIEWER)
    create_product({"designation": "Helm", "ei": "TST-E1"})
    create_product({"designation": "Gurt", "ei": "TST-E2"})

    page.goto(f"{site}/teile/")
    page.get_by_label("Helm markieren").check()
    with page.expect_download() as marked:
        page.get_by_role("button", name="Markierte als CSV").click()
    path = tmp_path / "markiert.csv"
    marked.value.save_as(path)
    text = path.read_text(encoding="utf-8-sig")
    assert "TST-E1" in text and "TST-E2" not in text

    with page.expect_download() as everything:
        page.get_by_role("button", name="PDF", exact=True).click()
    assert everything.value.suggested_filename.endswith(".pdf")


def test_admin_downloads_backup(page: Page, site: str, tmp_path: Path) -> None:
    sign_in(page, site, Role.ADMIN)
    create_product({"designation": "Helm", "ei": "TST-E3"})

    page.goto(f"{site}/verwaltung/nutzer/")
    page.get_by_role("link", name="Vollständige Sicherung").click()
    expect(page.get_by_text("Enthält Personendaten")).to_be_visible()
    with page.expect_download() as download:
        page.get_by_role("button", name="Sicherung herunterladen").click()
    path = tmp_path / "sicherung.zip"
    download.value.save_as(path)

    with zipfile.ZipFile(path) as archive:
        assert "daten.json" in archive.namelist()
        assert b"TST-E3" in archive.read("daten.json")
