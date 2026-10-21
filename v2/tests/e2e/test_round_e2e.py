"""Ende-zu-Ende: Prüfmodus (Parität 7) auf Desktop, Handy und Tablet.

Gescannt wird hier über das EI-Feld (wie ein Handscanner); die Kamera lässt sich im Testbrowser
nicht bedienen. Dass der QR-Decoder unter der Content-Security-Policy läuft und einen Code
unserer Etiketten liest, prüft ein eigener Test.
"""

from datetime import timedelta

import pytest
import segno
from django.utils import timezone
from playwright.sync_api import Page, expect

from accounts.models import Qualification, Role, User
from inspections.models import InspectionRound, RoundState
from inventory.services import create_product
from tests.e2e.conftest import assert_fits_width, sign_in

pytestmark = [pytest.mark.e2e, pytest.mark.django_db(transaction=True)]


def qualify(user: User) -> None:
    today = timezone.localdate()
    Qualification.objects.create(
        user=user,
        valid_from=today - timedelta(days=1),
        valid_until=today + timedelta(days=365),
        evidence="Lehrgang PSA-Sachkunde (Testdaten)",
    )


def test_parity_7_round_on_devices(device_page: Page, site: str) -> None:
    page = device_page
    expert = sign_in(page, site, Role.PPE_EXPERT)
    qualify(expert)
    create_product({"designation": "Bandschlinge", "ei": "TST-R1"})
    create_product({"designation": "Karabiner", "ei": "TST-R2"})

    page.goto(f"{site}/pruefung/runde/")
    page.get_by_role("button", name="Prüfungsrunde beginnen").click()
    for code in ("tst-r1", "https://psagent.example.org/q/TST-R2"):
        page.get_by_label("EI (tippen, Handscanner oder Kamera)").fill(code)
        page.get_by_role("button", name="Hinzufügen").click()
        expect(page.get_by_label("EI (tippen, Handscanner oder Kamera)")).to_have_value("")
    expect(page.get_by_role("heading", name="Noch ohne Ergebnis (2)")).to_be_visible()
    assert_fits_width(page)

    first = page.locator("article.round-part").first
    first.get_by_label("Mangel, sperren").check()
    first.get_by_label("Kommentar (Pflicht bei Mangel und Aussondern)").fill("Naht offen")
    first.get_by_role("button", name="Speichern").click()
    expect(page.get_by_role("heading", name="Erfasst (1)")).to_be_visible()

    second = page.locator("article.round-part").first
    second.get_by_role("button", name="Speichern").click()  # ohne Wahl: Browser hält an
    expect(page.get_by_role("heading", name="Erfasst (1)")).to_be_visible()
    second.get_by_label("in Ordnung").check()
    second.get_by_role("button", name="+6 Monate").click()
    second.get_by_role("button", name="Speichern").click()
    expect(page.get_by_role("heading", name="Erfasst (2)")).to_be_visible()
    assert_fits_width(page)

    page.get_by_role("button", name="Runde abschließen").click()

    expect(page.get_by_text("sobald sie signiert ist")).to_be_visible()
    round_ = InspectionRound.objects.get(inspector=expert)
    assert round_.state() == RoundState.CLOSED
    due = {r.part.name: r.next_due for r in round_.current_results()}
    assert due["Karabiner"] is not None and due["Karabiner"] < timezone.localdate() + timedelta(
        days=200
    )


def test_qr_decoder_reads_our_labels(page: Page, site: str) -> None:
    expert = sign_in(page, site, Role.PPE_EXPERT)
    qualify(expert)
    page.goto(f"{site}/pruefung/runde/")
    page.get_by_role("button", name="Prüfungsrunde beginnen").click()
    # Als Funktion, nicht als Ausdruck: Den Ausdruck wertet Playwright per eval aus, und das
    # verbietet die Content Security Policy der Seite (neuere Chromium-Versionen).
    page.wait_for_function("() => typeof window.jsQR === 'function'")
    content = "https://psagent.example.org/q/TST-QR1"

    decoded = page.evaluate(
        """async (src) => {
            const image = new Image();
            image.src = src;
            await image.decode();
            const canvas = document.createElement("canvas");
            canvas.width = image.width;
            canvas.height = image.height;
            const context = canvas.getContext("2d");
            context.drawImage(image, 0, 0);
            const data = context.getImageData(0, 0, canvas.width, canvas.height);
            const code = window.jsQR(data.data, data.width, data.height);
            return code && code.data;
        }""",
        segno.make(content, error="m").png_data_uri(scale=6, border=2),
    )

    assert decoded == content


def test_parity_8_protocol_download_after_closing(page: Page, site: str) -> None:
    """#38 Schritt 1: Nach dem Abschluss steht das Prüfprotokoll zum Signieren bereit."""
    from inspections import services
    from inspections.models import Outcome

    expert = sign_in(page, site, Role.PPE_EXPERT)
    qualify(expert)
    part = create_product({"designation": "Bandschlinge", "ei": "TST-P8"})
    round_ = services.start_round(actor=expert)
    services.record_result(
        round_,
        part,
        Outcome.PASSED,
        actor=expert,
        next_due=timezone.localdate() + timedelta(days=365),
    )
    services.close_round(round_, actor=expert)

    page.goto(f"{site}/pruefung/runde/")
    with page.expect_download() as download:
        page.get_by_role("link", name="Prüfprotokoll zum Signieren (PDF)").click()

    assert download.value.suggested_filename == f"Pruefprotokoll_Runde_{round_.pk}.pdf"
