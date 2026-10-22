"""Ende-zu-Ende: Prüfberichte (Parität 9, #39) – Liste, Bericht öffnen, Knopf „Unverändert?“."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from django.db import connection
from django.utils import timezone
from playwright.sync_api import Page, expect

from accounts.models import Role, User
from inspections import services
from inspections.models import InspectionRound, Outcome
from inventory.services import create_product
from tests.e2e.conftest import assert_fits_width, sign_in
from tests.helpers import make_inspector
from tests.signing import sign, signing_environment

pytestmark = [pytest.mark.e2e, pytest.mark.django_db(transaction=True)]


def sign_round_in_thread(round_: InspectionRound, inspector: User) -> None:
    """Signiert und schließt danach die Datenbankverbindung des Hilfsthreads."""
    try:
        sign(round_, actor=inspector)
    finally:
        connection.close()


def test_parity_9_open_report_and_check(device_page: Page, site: str) -> None:
    page = device_page
    inspector = make_inspector()
    part = create_product({"designation": "Bandschlinge", "ei": "TST-B9"})
    round_ = services.start_round(actor=inspector)
    services.record_result(
        round_,
        part,
        Outcome.PASSED,
        actor=inspector,
        next_due=timezone.localdate() + timedelta(days=365),
    )
    services.close_round(round_, actor=inspector)
    # pyHanko signiert mit asyncio; im Testthread läuft schon die Schleife von Playwright.
    with ThreadPoolExecutor(max_workers=1) as worker:
        worker.submit(sign_round_in_thread, round_, inspector).result()
    sign_in(page, site, Role.VIEWER)

    page.goto(f"{site}/")
    page.get_by_role("link", name="Prüfberichte").click()
    page.get_by_label("Teil oder EI").fill("tst-b9")
    page.get_by_role("button", name="Filtern").click()
    assert_fits_width(page)
    page.get_by_role("link", name=str(round_)).click()

    expect(page.get_by_role("cell", name="TST-B9")).to_be_visible()
    with signing_environment():
        page.get_by_role("button", name="Unverändert?").click()
        expect(page.get_by_role("status")).to_contain_text("Unverändert.")
    expect(page.get_by_role("status")).to_contain_text("Zeitstempel gültig")
    assert_fits_width(page)
