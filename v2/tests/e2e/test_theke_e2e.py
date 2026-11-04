"""Ende-zu-Ende: Ausgabe an der Theke im Tablet-Format 1280 x 800 (#127, PR 1).

Abläufe aus dem Prototyp: Ausgabe mit Gurtgrößen, Teil per EI-Tastatur, Person anlegen,
Reservierung abholen. Die Unterschrift wird mit der Maus gezeichnet, wie mit Finger oder Stift.
"""

from datetime import timedelta

import pytest
from django.utils import timezone
from playwright.sync_api import Page, ViewportSize, expect

from accounts.models import Role
from inventory.models import Part
from inventory.services import create_product, link_document
from lending.models import Borrower, Loan, LoanStatus
from lending.services import create_borrower, reserve
from tests.e2e.conftest import assert_fits_width, sign_in
from tests.helpers import adopt_inspection

pytestmark = [pytest.mark.e2e, pytest.mark.django_db(transaction=True)]
TABLET: ViewportSize = {"width": 1280, "height": 800}


def ready_part(ei: str, designation: str, size: str = "") -> Part:
    part = create_product({"designation": designation, "ei": ei, "size": size})
    part.documents.add(link_document(f"https://example.org/gal/{ei}.pdf", title=f"GAL {ei}"))
    adopt_inspection([part], next_due=timezone.localdate() + timedelta(days=365))
    return part


def known_borrower() -> Borrower:
    return create_borrower(
        "Kim Beispiel", address="Teststraße 1, 00000 Beispielstadt", email="kim@example.org"
    )


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


def choose_period(page: Page, label: str) -> None:
    """Leihdauer antippen und warten, bis Schritt „Teile“ samt Skripten geladen ist.

    `theke.js` kommt mit `defer`: Ein Klick auf eine Art-Taste, bevor das Skript läuft, geht
    verloren (in der CI einmal passiert, EI „DAV-HL-21“ statt „DAV-HL-S-21“).
    """
    page.get_by_role("button", name=label).click()
    page.wait_for_url("**/theke/ausgabe/teile/")


def follow(page: Page, link: str) -> None:
    """Link antippen und warten, bis die neue Seite samt htmx geladen ist."""
    page.get_by_role("link", name=link).click()
    page.wait_for_load_state("load")


def finish_signature(page: Page) -> None:
    page.get_by_label("Kurzkontrolle durchgeführt").check()
    page.get_by_label("GAL bereitgestellt").check()
    sign(page)
    page.get_by_role("button", name="Ausgeben").click()


def test_checkout_with_belt_sizes(page: Page, site: str) -> None:
    """Dauer → Gurt nach Größe und Helm → bekannte Person bestätigen → Unterschrift."""
    page.set_viewport_size(TABLET)
    sign_in(page, site, Role.LENDER)
    ready_part("DAV-HL-G-1", "Klettergurt", size="M-XL")
    exact = ready_part("DAV-HL-G-2", "Klettergurt", size="M")
    helmet = ready_part("DAV-HL-H-31", "Kletterhelm")
    known_borrower()

    page.goto(f"{site}/theke/")
    assert_fits_width(page)
    page.get_by_role("button", name="Ausgabe").click()
    choose_period(page, "1 Woche")

    page.get_by_role("button", name="Klettergurt Größe M hinzufügen, 2 frei").click()
    expect(page.get_by_text("DAV-HL-G-2")).to_be_visible()
    expect(page.get_by_text("Größe M · Vorschlag, passt genau")).to_be_visible()
    page.get_by_role("button", name="Helm eins mehr").click()
    expect(page.get_by_text("DAV-HL-H-31")).to_be_visible()
    assert_fits_width(page)
    follow(page, "Weiter: Entleiher")

    page.get_by_label("Name suchen (ab 3 Zeichen) oder Mitgliedsausweis scannen").fill("Kim")
    page.get_by_role("button", name="Kim Beispiel").click()
    page.get_by_role("button", name="unverändert (nachgefragt)").click()
    expect(page.get_by_text("Kontaktdaten bestätigt.")).to_be_visible()
    follow(page, "Weiter: Unterschrift")

    assert_fits_width(page)
    finish_signature(page)

    expect(page.get_by_text("Ausgegeben: 2 Teile")).to_be_visible()
    for part in (exact, helmet):
        assert Loan.objects.get(part=part).status == LoanStatus.LENT


def test_other_part_by_keypad_and_new_person(page: Page, site: str) -> None:
    """EI über Art-Taste und Ziffernblock, Vorschlag; danach eine neue Person anlegen."""
    page.set_viewport_size(TABLET)
    sign_in(page, site, Role.LENDER)
    rope = ready_part("DAV-HL-S-21", "Einfachseil 50 m")

    page.goto(f"{site}/theke/")
    page.get_by_role("button", name="Ausgabe").click()
    choose_period(page, "3 Tage")
    page.get_by_role("button", name="Art Seil").click()
    page.get_by_role("button", name="Ziffer 2").click()
    page.get_by_role("button", name="Ziffer 1").click()
    expect(page.locator("#ei-eingabe")).to_have_value("DAV-HL-S-21")
    expect(page.locator("#ei-vorschlaege").get_by_text("Einfachseil 50 m")).to_be_visible()
    page.get_by_role("button", name="Hinzufügen", exact=True).click()
    expect(page.locator("#schritt").get_by_text("DAV-HL-S-21")).to_be_visible()
    expect(page.locator("#ei-eingabe")).to_have_value("DAV-HL-")
    follow(page, "Weiter: Entleiher")

    page.locator("#id_name").fill("Mara Muster")
    page.get_by_label("Adresse").fill("Teststraße 2, 00000 Beispielstadt")
    page.get_by_label("Telefon").fill("0000 000000")
    page.get_by_role("button", name="Anlegen").click()
    expect(page.get_by_text("Kontaktdaten bestätigt.")).to_be_visible()
    follow(page, "Weiter: Unterschrift")
    finish_signature(page)

    expect(page.get_by_text("Ausgegeben: 1 Teil")).to_be_visible()
    loan = Loan.objects.get(part=rope)
    assert loan.booking.borrower.display_name == "Mara Muster"


def test_unknown_ei_keeps_the_entry(page: Page, site: str) -> None:
    page.set_viewport_size(TABLET)
    sign_in(page, site, Role.LENDER)

    page.goto(f"{site}/theke/")
    page.get_by_role("button", name="Ausgabe").click()
    choose_period(page, "1 Woche")
    page.locator("#ei-eingabe").fill("DAV-HL-H-999")
    page.get_by_role("button", name="Hinzufügen", exact=True).click()

    expect(page.get_by_role("alert")).to_contain_text("DAV-HL-H-999 gibt es nicht")
    expect(page.locator("#ei-eingabe")).to_have_value("DAV-HL-H-999")


def test_pick_up_a_reservation(page: Page, site: str) -> None:
    page.set_viewport_size(TABLET)
    lender = sign_in(page, site, Role.LENDER)
    helmet = ready_part("DAV-HL-H-31", "Kletterhelm")
    today = timezone.localdate()
    reserve(known_borrower(), [helmet], today, today + timedelta(days=2), actor=lender)

    page.goto(f"{site}/theke/")
    page.get_by_role("button", name="Ausgabe").click()
    follow(page, "Reservierung abholen")
    page.get_by_role("button", name="Kim Beispiel").click()
    page.get_by_role("button", name="unverändert (nachgefragt)").click()
    follow(page, "Weiter: Unterschrift")
    expect(page.get_by_label("DAV-HL-H-31 Kletterhelm", exact=False)).to_be_checked()
    finish_signature(page)

    expect(page.get_by_text("Ausgegeben: 1 Teil")).to_be_visible()
    assert Loan.objects.get(part=helmet).status == LoanStatus.LENT
