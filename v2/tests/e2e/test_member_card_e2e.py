"""Ende-zu-Ende: Mitgliedsausweis mit der Kamera oder dem Handscanner lesen (#125).

Chromium unter Linux hat keinen `BarcodeDetector`. Der Test ersetzt ihn und die Kamera durch
Attrappen: Die Kamera liefert ein leeres Bild, der Detektor „liest“ die Zeile aus
`window.__card`. So prüft der Test den Weg vom Kamera-Knopf bis zum Server, nicht die
Bilderkennung des Browsers.

Der Strichcode enthält auch Kategorie, Eintrittsjahre und Geburtsdatum (Felix, 03.11.2026). Die
Tests zeichnen jede Anfrage des Browsers auf: Das Geburtsdatum darf in keiner vorkommen.
"""

import pytest
from playwright.sync_api import Page, expect

from accounts.models import Role
from lending import members
from lending.models import Borrower, BorrowerContact
from lending.services import create_borrower
from tests.e2e.conftest import assert_fits_width, sign_in

pytestmark = [pytest.mark.e2e, pytest.mark.django_db(transaction=True)]
NUMBER = "99900004711"
BIRTH = "01011990"
CARD_LINE = f"999/00/004711*1000*2020*2020*{BIRTH}"  # künstlich, Aufbau wie beim DAV-Muster

FAKE_CAMERA = """
(() => {
  class FakeBarcodeDetector {
    static getSupportedFormats() { return Promise.resolve(["code_128", "ean_13", "qr_code"]); }
    detect() {
      return Promise.resolve(window.__card ? [{ rawValue: window.__card }] : []);
    }
  }
  window.BarcodeDetector = FakeBarcodeDetector;
  navigator.mediaDevices.getUserMedia = () => {
    const canvas = document.createElement("canvas");
    canvas.width = 64;
    canvas.height = 48;
    const context = canvas.getContext("2d");
    window.setInterval(() => context.fillRect(0, 0, 64, 48), 50);
    return Promise.resolve(canvas.captureStream(10));
  };
})();
"""


def record_requests(page: Page) -> list[str]:
    """Adresse und Inhalt jeder Anfrage, die der Browser an den Server schickt."""
    sent: list[str] = []
    page.on("request", lambda request: sent.append(request.url + (request.post_data or "")))
    return sent


def test_scan_finds_the_linked_person_at_checkout(device_page: Page, site: str) -> None:
    """Ausgabe: Ausweis vor die Kamera, die verbundene Person steht zur Auswahl."""
    page = device_page
    lender = sign_in(page, site, Role.LENDER)
    borrower = create_borrower("Kim Beispiel", address="Teststraße 1", actor=lender)
    members.link(borrower, NUMBER, actor=lender)
    page.add_init_script(FAKE_CAMERA)
    sent = record_requests(page)

    page.goto(f"{site}/verleih/neu/")
    page.evaluate(f"window.__card = '{CARD_LINE}'")
    page.get_by_role("button", name="Ausweis mit der Kamera lesen").click()

    expect(page.get_by_text("Ausweis gelesen.")).to_be_visible()
    expect(page.locator("#borrower-search")).to_have_value("999/00/004711")
    expect(page.get_by_label("Kim Beispiel")).to_be_visible()
    assert_fits_width(page)
    assert not [request for request in sent if BIRTH in request]


@pytest.mark.parametrize("enter", [True, False], ids=["mit Enter", "ohne Enter"])
def test_hand_scanner_line_is_cut_before_the_search_sends(
    page: Page, site: str, enter: bool
) -> None:
    """Handscanner: tippt die ganze Zeile, oft mit Enter. Gesucht wird nur mit der Nummer, das
    Formular geht nicht ab, und das Geburtsdatum verlässt den Browser nicht."""
    lender = sign_in(page, site, Role.LENDER)
    borrower = create_borrower("Kim Beispiel", address="Teststraße 1", actor=lender)
    members.link(borrower, NUMBER, actor=lender)
    sent = record_requests(page)

    page.goto(f"{site}/verleih/neu/")
    page.locator("#borrower-search").focus()
    page.keyboard.type(CARD_LINE)
    if enter:
        page.keyboard.press("Enter")

    expect(page.get_by_label("Kim Beispiel")).to_be_visible()
    expect(page.locator("#borrower-search")).to_have_value("999/00/004711")
    expect(page.get_by_role("heading", name="Reservieren oder ausgeben")).to_be_visible()
    assert not [request for request in sent if BIRTH in request]
    assert not [request for request in sent if "/verleih/neu/" in request and "suche=" in request]


def test_unknown_barcode_shows_only_its_shape(page: Page, site: str) -> None:
    """Ein anderer Strichcode: Hinweis mit dem Aufbau ohne Ziffern, das Feld bleibt leer."""
    sign_in(page, site, Role.LENDER)
    page.add_init_script(FAKE_CAMERA)

    page.goto(f"{site}/verleih/entleiher/neu/")
    page.evaluate("window.__card = '4006381333931'")
    page.get_by_role("button", name="Ausweis mit der Kamera lesen").click()

    expect(page.get_by_text("Aufbau: 9999999999999")).to_be_visible()
    expect(page.get_by_label("Mitgliedsnummer (DAV-Ausweis scannen oder eintippen)")).to_have_value(
        ""
    )


def test_new_person_with_scanned_card(page: Page, site: str) -> None:
    """Person anlegen, Ausweis lesen, speichern: Die Nummer hängt an der neuen Person."""
    sign_in(page, site, Role.LENDER)
    page.add_init_script(FAKE_CAMERA)

    page.goto(f"{site}/verleih/entleiher/neu/")
    page.get_by_label("Name").fill("Mara Muster")
    page.get_by_label("Adresse").fill("Teststraße 2, 00000 Beispielstadt")
    sent = record_requests(page)
    page.evaluate("window.__card = '999/00/008150*1000*2020*2020*01011990'")
    page.get_by_role("button", name="Ausweis mit der Kamera lesen").click()
    expect(page.get_by_text("Ausweis gelesen.")).to_be_visible()
    page.get_by_role("button", name="Anlegen").click()

    expect(page.get_by_text("Mara Muster ist angelegt.")).to_be_visible()
    person = Borrower.objects.get(contact__name="Mara Muster")
    assert BorrowerContact.objects.get(borrower=person).member_number == "99900008150"
    assert not [request for request in sent if BIRTH in request]


def test_link_card_after_name_check(page: Page, site: str) -> None:
    """Bekannte Person: Ausweis lesen, Namen abgleichen, verbinden."""
    lender = sign_in(page, site, Role.LENDER)
    borrower = create_borrower("Kim Beispiel", address="Teststraße 1", actor=lender)
    page.add_init_script(FAKE_CAMERA)

    page.goto(f"{site}/verleih/entleiher/{borrower.pk}/ausweis/")
    page.evaluate(f"window.__card = '{NUMBER}'")
    page.get_by_role("button", name="Ausweis mit der Kamera lesen").click()
    expect(page.get_by_text("Ausweis gelesen.")).to_be_visible()
    page.get_by_label("Der Name auf dem Ausweis stimmt mit dieser Person überein").check()
    page.get_by_role("button", name="Ausweis verbinden").click()

    expect(page.get_by_text("Mitgliedsausweis gespeichert.")).to_be_visible()
    assert BorrowerContact.objects.get(borrower=borrower).member_number == NUMBER


def test_without_barcode_detector_the_field_stays(page: Page, site: str) -> None:
    """Browser ohne Strichcode-Erkennung: Hinweis auf Handscanner und Eintippen."""
    sign_in(page, site, Role.LENDER)
    page.add_init_script("delete window.BarcodeDetector;")

    page.goto(f"{site}/verleih/entleiher/neu/")
    page.get_by_role("button", name="Ausweis mit der Kamera lesen").click()

    expect(
        page.get_by_text("Dieser Browser liest keine Strichcodes mit der Kamera.")
    ).to_be_visible()
    expect(
        page.get_by_label("Mitgliedsnummer (DAV-Ausweis scannen oder eintippen)")
    ).to_be_editable()
