"""Ende-zu-Ende: PDF-Dokumente auf der Teileseite herunterladen (Parität 5)."""

from datetime import timedelta
from pathlib import Path

import pytest
from django.utils import timezone
from playwright.sync_api import Page, expect

from accounts.models import Qualification, Role
from inventory.services import create_product
from tests.e2e.conftest import assert_fits_width, sign_in

pytestmark = [pytest.mark.e2e, pytest.mark.django_db(transaction=True)]


def test_expert_downloads_documents(device_page: Page, site: str, tmp_path: Path) -> None:
    page = device_page
    expert = sign_in(page, site, Role.PPE_EXPERT, stamp_text="Testperson Stempel")
    today = timezone.localdate()
    Qualification.objects.create(
        user=expert,
        valid_from=today - timedelta(days=1),
        valid_until=today + timedelta(days=365),
        evidence="Lehrgang PSA-Sachkunde (Testdaten)",
    )
    part = create_product({"designation": "Klettergurt", "ei": "TST-DOK1"})

    page.goto(f"{site}/teile/{part.pk}/")
    expect(page.get_by_role("heading", name="Dokumente")).to_be_visible()
    assert_fits_width(page)
    for link, suffix in (
        ("Einsatzdokumentation (PDF)", "Einsatzdokumentation"),
        ("PSA-Produktdatenblatt (PD, PDF)", "PD"),
        ("Haftungsausschluss (PDF)", "Haftungsausschluss"),
    ):
        with page.expect_download() as download:
            page.get_by_role("link", name=link).click()
        assert download.value.suggested_filename == f"TST-DOK1_{suffix}.pdf"
        path = tmp_path / f"{suffix}.pdf"
        download.value.save_as(path)
        assert path.read_bytes().startswith(b"%PDF-")


def test_trainer_sees_only_usage_log(page: Page, site: str) -> None:
    sign_in(page, site, Role.TRAINER)
    part = create_product({"designation": "Klettergurt", "ei": "TST-DOK2"})

    page.goto(f"{site}/teile/{part.pk}/")

    expect(page.get_by_role("link", name="Einsatzdokumentation (PDF)")).to_be_visible()
    expect(page.get_by_role("link", name="Haftungsausschluss (PDF)")).to_have_count(0)
    expect(page.get_by_text("PD und Haftungsausschluss erzeugen Personen mit")).to_be_visible()
