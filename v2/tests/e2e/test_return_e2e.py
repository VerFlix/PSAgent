"""Ende-zu-Ende: Reservieren → Ausgeben → Rückgabe (#13; Parität 15, 17, 19).

Ausgabe am Tablet, Rückgabe am Handy mit Sperre; die Detailseite zeigt beide Unterschriften.
"""

from collections.abc import Callable, Iterator
from datetime import timedelta

import pytest
from django.utils import timezone
from playwright.sync_api import Browser, BrowserContext, Page, Playwright, expect

from accounts.models import Role
from lending.models import Loan, LoanStatus
from lending.services import create_borrower, reserve
from tests.e2e.conftest import assert_fits_width, sign_in
from tests.e2e.test_lending_e2e import ready_part, sign

pytestmark = [pytest.mark.e2e, pytest.mark.django_db(transaction=True)]


@pytest.fixture
def device(browser: Browser, playwright: Playwright) -> Iterator[Callable[[str], Page]]:
    """Seiten mit Geräteprofil; alle Kontexte werden am Ende geschlossen."""
    contexts: list[BrowserContext] = []

    def open_page(name: str) -> Page:
        profile = dict(playwright.devices[name])
        profile.pop("default_browser_type", None)
        contexts.append(browser.new_context(**profile))
        return contexts[-1].new_page()

    yield open_page
    for context in contexts:
        context.close()


def test_reserve_checkout_return(site: str, device: Callable[[str], Page]) -> None:
    tablet = device("iPad Mini")
    lender = sign_in(tablet, site, Role.LENDER)
    part = ready_part("TST-R1", designation="Helm")
    borrower = create_borrower("Kim Beispiel", address="Teststraße 1", email="kim@example.org")
    today = timezone.localdate()
    booking = reserve(borrower, [part], today, today + timedelta(days=2), actor=lender)

    tablet.goto(f"{site}/verleih/{booking.pk}/")
    tablet.get_by_role("link", name="Reservierung ausgeben").click()
    tablet.get_by_label("unverändert (nachgefragt)").check()
    tablet.get_by_label("Kurzkontrolle durchgeführt").check()
    tablet.get_by_label("GAL bereitgestellt").check()
    sign(tablet)
    tablet.get_by_role("button", name="Ausgeben", exact=True).click()
    expect(tablet.get_by_text("Ausgegeben: 1 Teil(e).")).to_be_visible()
    assert_fits_width(tablet)

    phone = device("Pixel 7")
    phone.context.add_cookies(  # dieselbe Person, anderes Gerät
        [{"name": c["name"], "value": c["value"], "url": site} for c in tablet.context.cookies()]
    )
    phone.goto(f"{site}/verleih/{booking.pk}/")
    phone.get_by_role("link", name="Zurücknehmen").click()
    phone.get_by_label("Helm sperren").check()
    phone.get_by_label("Grund der Sperre (für markierte Teile)").fill("Riss in der Schale")
    phone.get_by_label("Kurzprüfung durchgeführt").check()
    sign(phone)
    assert_fits_width(phone)
    phone.get_by_role("button", name="Zurücknehmen").click()

    expect(phone.get_by_text("Zurückgenommen: 1 Teil(e), davon 1 gesperrt.")).to_be_visible()
    expect(phone.locator("svg.signature-view")).to_have_count(2)
    assert_fits_width(phone)
    assert Loan.objects.get().status == LoanStatus.RETURNED
    assert part.is_locked()
