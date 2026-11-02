"""Ende-zu-Ende: Prüfprotokoll aus dem Notbetrieb nacherfassen (#80, Parität 22).

Der PSA-Prüfer erfasst sein Formular P; daraus wird eine abgeschlossene Prüfungsrunde, die er
anschließend wie jede Runde signiert.
"""

from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from django.utils import timezone
from playwright.sync_api import Page, expect

from accounts.models import Qualification, Role
from emergency.binder import create_binder
from emergency.models import InspectionForm
from inspections.models import RoundState
from inventory.services import create_product
from tests.e2e.conftest import sign_in

pytestmark = [pytest.mark.e2e, pytest.mark.django_db(transaction=True)]
SCAN = b"%PDF-1.4\n% Scan Pruefprotokoll, kuenstliche Testdaten\n%%EOF\n"


def test_expert_backfills_inspection_form(
    page: Page, site: str, tmp_path: Path, settings: Any
) -> None:
    settings.MEDIA_ROOT = tmp_path / "media"
    create_product({"designation": "Klettergurt", "ei": "TST-NP1"})
    create_binder(actor=None)
    scan = tmp_path / "scan.pdf"
    scan.write_bytes(SCAN)
    today = timezone.localdate()
    expert = sign_in(page, site, Role.PPE_EXPERT, username="e2e-pruefer")
    Qualification.objects.create(
        user=expert,
        valid_from=today - timedelta(days=365),
        valid_until=today + timedelta(days=365),
        evidence="Lehrgang PSA-Sachkunde (Testdaten)",
    )

    page.goto(f"{site}/notbetrieb/")
    page.get_by_role("link", name="Prüfprotokolle nacherfassen").click()
    page.get_by_label("Formularnummer (z. B. P-00002)").fill("P-00001")
    page.get_by_label("Scan als PDF oder Fotos (JPEG, PNG)").set_input_files(str(scan))
    page.get_by_label("Datum der Prüfung").fill(today.isoformat())
    page.locator("#id_rows-0-ei").fill("TST-NP1")
    page.locator("#id_rows-0-outcome").select_option(label="in Ordnung")
    page.locator("#id_rows-0-next_due").fill((today + timedelta(days=365)).isoformat())
    page.get_by_role("button", name="Nacherfassen").click()

    expect(page.get_by_text("Formular P-00001 als Prüfungsrunde")).to_be_visible()
    form = InspectionForm.objects.get()
    assert form.inspector == expert and form.round.state() == RoundState.CLOSED
