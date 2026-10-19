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

    page.locator("article.round-part").first.get_by_role("button", name="+6 Monate").click()
    page.locator("article.round-part").first.get_by_role("button", name="Speichern").click()
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
    page.wait_for_function("typeof window.jsQR === 'function'")
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
