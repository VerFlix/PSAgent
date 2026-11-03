"""Ende-zu-Ende: Rückgabe und Schaden melden an der Theke, Tablet 1280 x 800 (#127, Teil 2).

Abläufe aus dem Prototyp: Rückgabe überfällig und teilweise, Rückgabe mit Schaden (sperrt),
Schaden melden mit Grund und Kommentar.
"""

from datetime import timedelta

import pytest
from django.utils import timezone
from playwright.sync_api import Page, expect

from accounts.models import Role
from inventory.models import EventKind, PartEvent
from lending.models import Loan, LoanStatus
from lending.services import lend
from tests.e2e.conftest import assert_fits_width, sign_in
from tests.e2e.test_theke_e2e import TABLET, known_borrower, ready_part, sign

pytestmark = [pytest.mark.e2e, pytest.mark.django_db(transaction=True)]


def lent_since(days_ago: int, due_days_ago: int, *eis: tuple[str, str]) -> list[Loan]:
    """Ausleihe, die vor `days_ago` Tagen begann und vor `due_days_ago` Tagen fällig war."""
    today = timezone.localdate()
    parts = [ready_part(ei, name) for ei, name in eis]
    booking = lend(
        known_borrower(),
        parts,
        today - timedelta(days=days_ago),
        today - timedelta(days=due_days_ago),
        actor=None,
        quick_check=True,
        gal_provided=True,
        signature=[[[10, 10], [20, 20]]],
        signer_name="Kim Beispiel",
    )
    return list(booking.loans.order_by("pk"))


def finish_return(page: Page) -> None:
    page.get_by_label("Kurzprüfung durchgeführt").check()
    sign(page)
    page.get_by_role("button", name="Zurücknehmen").click()


def test_overdue_partial_return(page: Page, site: str) -> None:
    page.set_viewport_size(TABLET)
    sign_in(page, site, Role.LENDER)
    helmet, rope = lent_since(5, 3, ("DAV-HL-H-31", "Kletterhelm"), ("DAV-HL-S-21", "Seil 50 m"))

    page.goto(f"{site}/theke/")
    page.get_by_role("link", name="Rückgabe").first.click()
    page.wait_for_load_state("load")
    expect(page.get_by_text("3 Tage überfällig")).to_be_visible()
    assert_fits_width(page)
    page.get_by_role("link", name="Kim Beispiel").click()
    page.wait_for_load_state("load")
    page.get_by_label("DAV-HL-H-31 Kletterhelm", exact=False).check()
    assert_fits_width(page)
    finish_return(page)

    expect(page.get_by_text("Zurückgenommen: 1 Teil.")).to_be_visible()
    statuses = [Loan.objects.get(pk=loan.pk).status for loan in (helmet, rope)]
    assert statuses == [LoanStatus.RETURNED, LoanStatus.LENT]


def test_return_by_scanning_with_damage(page: Page, site: str) -> None:
    """Teil per EI-Tastatur in der Liste scannen, Schaden mit Grund: das Teil wird gesperrt."""
    page.set_viewport_size(TABLET)
    sign_in(page, site, Role.LENDER)
    (helmet,) = lent_since(2, 0, ("DAV-HL-H-31", "Kletterhelm"))

    page.goto(f"{site}/theke/rueckgabe/")
    page.get_by_role("button", name="Art Helm").click()
    for digit in "31":
        page.get_by_role("button", name=f"Ziffer {digit}").click()
    page.get_by_role("button", name="Ausleihe öffnen").click()
    page.wait_for_load_state("load")
    expect(page.get_by_label("DAV-HL-H-31 Kletterhelm", exact=False)).to_be_checked()
    page.get_by_label("Schaden an DAV-HL-H-31").check()
    page.get_by_label("Sturz belastet").check()
    finish_return(page)

    expect(page.get_by_text("Zurückgenommen: 1 Teil, davon 1 gesperrt.")).to_be_visible()
    event = PartEvent.objects.get(part=helmet.part, kind=EventKind.LOCKED)
    assert event.comment == "Sturz belastet"


def test_report_damage(page: Page, site: str) -> None:
    page.set_viewport_size(TABLET)
    sign_in(page, site, Role.LENDER)
    rope = ready_part("DAV-HL-S-21", "Einfachseil 50 m")

    page.goto(f"{site}/theke/")
    page.get_by_role("link", name="Schaden melden").click()
    page.wait_for_load_state("load")
    page.get_by_role("button", name="Art Seil").click()
    for digit in "21":
        page.get_by_role("button", name=f"Ziffer {digit}").click()
    expect(page.locator("#ei-vorschlaege").get_by_text("Einfachseil 50 m")).to_be_visible()
    page.get_by_role("button", name="Teil wählen").click()
    expect(page.get_by_role("heading", name="DAV-HL-S-21 Einfachseil 50 m")).to_be_visible()
    page.get_by_label("Chemikalien / Hitze").check()
    page.get_by_label("Was ist beschädigt?").fill("Mantel verfärbt")
    assert_fits_width(page)
    page.get_by_role("button", name="Sperren").click()

    expect(page.get_by_text("Gesperrt: Einfachseil 50 m DAV-HL-S-21.")).to_be_visible()
    event = PartEvent.objects.get(part=rope, kind=EventKind.LOCKED)
    assert event.comment == "Chemikalien / Hitze: Mantel verfärbt"
