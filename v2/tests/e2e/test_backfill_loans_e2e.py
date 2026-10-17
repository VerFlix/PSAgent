"""Ende-zu-Ende: Ausgabe vom Papier nacherfassen, Doppelbuchung klären (Parität 22)."""

from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from django.utils import timezone
from playwright.sync_api import Page, expect

from accounts.models import Role
from emergency.binder import SHEETS_PER_BINDER
from emergency.models import Binder, FormSheet
from inventory.services import create_product, link_document
from lending.models import Loan, LoanStatus
from lending.services import create_borrower, reserve
from tests.e2e.conftest import assert_fits_width, sign_in
from tests.helpers import adopt_inspection

pytestmark = [pytest.mark.e2e, pytest.mark.django_db(transaction=True)]
SCAN = b"%PDF-1.4\n% Scan Ausgabeformular, kuenstliche Testdaten\n%%EOF\n"


def days(n: int) -> str:
    return (timezone.localdate() + timedelta(days=n)).isoformat()


def test_loan_backfill_and_double_booking(
    device_page: Page, site: str, tmp_path: Path, settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings.MEDIA_ROOT = tmp_path / "media"
    past = timezone.now() - timedelta(days=30)
    with monkeypatch.context() as patch:
        patch.setattr(timezone, "now", lambda: past)
        part = create_product({"designation": "Klettergurt", "ei": "TST-NA1"})
        part.documents.add(link_document("https://example.org/gal.pdf", title="GAL"))
        adopt_inspection([part], next_due=timezone.localdate() + timedelta(days=400))
    binder = Binder.objects.create(created_at=timezone.now() - timedelta(days=10), sha256="0" * 64)
    FormSheet.objects.bulk_create(
        FormSheet(binder=binder, kind=kind, number=n)
        for kind, count in SHEETS_PER_BINDER.items()
        for n in range(1, count + 1)
    )
    lender = sign_in(device_page, site, Role.LENDER)
    reserve(
        create_borrower("Alex Vorher"),
        [part],
        timezone.localdate() - timedelta(days=3),
        timezone.localdate() - timedelta(days=1),
        actor=lender,
    )
    scan = tmp_path / "scan.pdf"
    scan.write_bytes(SCAN)
    page = device_page

    page.goto(f"{site}/notbetrieb/")
    page.get_by_role("link", name="Ausgaben und Rückgaben nacherfassen").click()
    assert_fits_width(page)
    page.get_by_label("Formularnummer (z. B. A-00007)").fill("A-00001")
    page.get_by_label("Scan als PDF oder Fotos (JPEG, PNG)").set_input_files(str(scan))
    page.get_by_label("Teile (EI), je Zeile eine").fill("TST-NA1")
    page.get_by_label("Kurzkontrolle angekreuzt").check()
    page.get_by_label("GAL bereitgestellt angekreuzt").check()
    page.get_by_label("Datum Ausgabe").fill(days(-5))
    page.get_by_label("Rückgabe geplant am").fill(days(5))
    page.get_by_label("Entleiher: Name").fill("Noa Papier")
    page.get_by_label("Entleiher: Adresse").fill("Teststraße 2")
    page.get_by_label("Entleiher: Telefon").fill("0000 000000")
    page.get_by_label("Name ausgebende Person").fill("Testperson Ausgabe")
    page.get_by_role("button", name="Nacherfassen").click()
    expect(page.get_by_text("wartet die Ausgabe auf die Klärungsliste")).to_be_visible()

    page.context.clear_cookies()
    sign_in(page, site, Role.PPE_EXPERT, username="e2e-pruefer")
    page.goto(f"{site}/notbetrieb/klaerung/")
    expect(page.get_by_text("Doppelbuchung: Ausgabe aus Formular A-00001")).to_be_visible()
    assert_fits_width(page)
    page.get_by_label("Begründung").fill("Reservierung war hinfällig")
    page.get_by_role("button", name="Reservierung stornieren und Ausleihe übernehmen").click()
    expect(page.get_by_text("Reservierung storniert und Ausgabe eingetragen.")).to_be_visible()
    assert Loan.objects.get(part=part, status=LoanStatus.LENT)
