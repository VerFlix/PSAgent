"""Ende-zu-Ende: Reservieren und Ausgeben im Browser (#13; Parität 12–14).

Die Ausgabe läuft auch auf Handy und Tablet; die Unterschrift wird mit der Maus gezeichnet,
wie mit Finger oder Stift.
"""

from datetime import timedelta

import pytest
from django.utils import timezone
from playwright.sync_api import Page, expect

from accounts.models import Role
from inventory.models import Part
from inventory.services import create_product, link_document
from lending.models import BorrowerContact, Loan, LoanStatus
from lending.services import create_borrower
from tests.e2e.conftest import assert_fits_width, sign_in
from tests.helpers import adopt_inspection

pytestmark = [pytest.mark.e2e, pytest.mark.django_db(transaction=True)]


def ready_part(ei: str, designation: str = "Klettergurt") -> Part:
    part = create_product({"designation": designation, "ei": ei})
    part.documents.add(link_document(f"https://example.org/gal/{ei}.pdf", title=f"GAL {ei}"))
    adopt_inspection([part], next_due=timezone.localdate() + timedelta(days=365))
    return part


def sign(page: Page) -> None:
    canvas = page.locator("canvas.signature")
    canvas.scroll_into_view_if_needed()
    box = canvas.bounding_box()
    assert box is not None
    page.mouse.move(box["x"] + 20, box["y"] + 30)
    page.mouse.down()
    for step in range(1, 12):
        page.mouse.move(box["x"] + 20 + step * 15, box["y"] + 30 + (step % 3) * 10)
    page.mouse.up()


def test_lend_with_signature_on_devices(device_page: Page, site: str) -> None:
    """Parität 14: neuer Entleiher, Kurzkontrolle, GAL, Unterschrift – Desktop, Handy, Tablet."""
    page = device_page
    sign_in(page, site, Role.LENDER)
    part = ready_part("TST-V1")

    page.goto(f"{site}/verleih/neu/")
    page.get_by_role("button", name="Verfügbarkeit anzeigen").click()
    page.get_by_label("Klettergurt wählen").check()
    page.get_by_label("Name", exact=True).fill("Robin Exempel")
    page.get_by_label("Adresse").fill("Beispielweg 4, 00000 Teststadt")
    page.get_by_label("E-Mail").fill("robin@example.org")
    assert_fits_width(page)
    page.get_by_role("button", name="Weiter zur Ausgabe (ab heute)").click()

    expect(page.get_by_role("heading", name="Ausgabe")).to_be_visible()
    assert_fits_width(page)
    page.get_by_label("Kurzkontrolle durchgeführt").check()
    page.get_by_label("GAL bereitgestellt").check()
    sign(page)
    page.get_by_role("button", name="Ausgeben", exact=True).click()

    expect(page.get_by_text("Ausgegeben: 1 Teil(e).")).to_be_visible()
    loan = Loan.objects.get(part=part)
    assert loan.status == LoanStatus.LENT
    signature = loan.events.get().signature
    assert signature is not None
    strokes = signature.content.strokes
    assert len(strokes) == 1 and len(strokes[0]) >= 10


def test_known_borrower_via_search_and_confirmation(page: Page, site: str) -> None:
    """Parität 14/16: Suche per htmx, nur Name; Häkchen „unverändert“ mit Datum."""
    sign_in(page, site, Role.LENDER)
    ready_part("TST-V2")
    borrower = create_borrower(
        "Kim Beispiel", address="Teststraße 1, 00000 Beispielstadt", email="kim@example.org"
    )

    page.goto(f"{site}/verleih/neu/")
    page.get_by_role("button", name="Verfügbarkeit anzeigen").click()
    page.get_by_label("Klettergurt wählen").check()
    page.get_by_label("Bekannten Entleiher suchen").fill("Kim")
    page.get_by_label("Kim Beispiel").check()
    expect(page.get_by_text("Kontaktdaten vollständig.")).to_be_visible()
    expect(page.get_by_text("Teststraße")).to_have_count(0)
    page.get_by_label("unverändert (nachgefragt)").check()
    page.get_by_role("button", name="Weiter zur Ausgabe (ab heute)").click()
    page.get_by_label("Kurzkontrolle durchgeführt").check()
    page.get_by_label("GAL bereitgestellt").check()
    sign(page)
    page.get_by_role("button", name="Ausgeben", exact=True).click()

    expect(page.get_by_text("Ausgegeben: 1 Teil(e).")).to_be_visible()
    assert Loan.objects.get().booking.borrower == borrower
    assert BorrowerContact.objects.get(borrower=borrower).confirmed_at is not None


def test_reserve_on_phone(device_page: Page, site: str) -> None:
    page = device_page
    sign_in(page, site, Role.LENDER)
    ready_part("TST-V3", designation="Helm")
    start = timezone.localdate() + timedelta(days=3)

    page.goto(f"{site}/verleih/neu/")
    page.get_by_label("von").fill(start.isoformat())
    page.get_by_label("bis einschließlich").fill((start + timedelta(days=2)).isoformat())
    page.get_by_role("button", name="Verfügbarkeit anzeigen").click()
    page.get_by_label("Helm wählen").check()
    page.get_by_label("Name", exact=True).fill("Noa Probe")
    page.get_by_role("button", name="Reservieren").click()

    expect(page.get_by_text("Reserviert: 1 Teil(e)")).to_be_visible()
    assert_fits_width(page)
    assert Loan.objects.get().status == LoanStatus.RESERVED
