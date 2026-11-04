"""Ende-zu-Ende: Ausleihe verlängern am PC und an der Theke (#129)."""

from datetime import timedelta

import pytest
from django.utils import timezone
from playwright.sync_api import Page, expect

from accounts.models import Role
from lending.models import Loan, LoanExtension
from tests.e2e.conftest import assert_fits_width, sign_in
from tests.e2e.test_theke_e2e import TABLET
from tests.e2e.test_theke_return_e2e import lent_since

pytestmark = [pytest.mark.e2e, pytest.mark.django_db(transaction=True)]


def test_extend_at_the_pc(page: Page, site: str) -> None:
    sign_in(page, site, Role.LENDER)
    (loan,) = lent_since(3, -2, ("DAV-HL-H-31", "Kletterhelm"))  # noch 2 Tage

    page.goto(f"{site}/verleih/{loan.booking_id}/")
    page.get_by_role("link", name="Verlängern").click()
    page.wait_for_load_state("load")
    new_end = timezone.localdate() + timedelta(days=9)
    expect(page.get_by_label("Neu zurück bis")).to_have_value(new_end.isoformat())
    page.get_by_label("telefonisch").check()
    page.get_by_role("button", name="Verlängern").click()

    expect(page.get_by_text(f"Verlängert: 1 Teil bis {new_end:%d.%m.%Y}.")).to_be_visible()
    expect(page.get_by_text("bei der Ausgabe unterschrieben")).to_be_visible()
    assert Loan.objects.get(pk=loan.pk).end == new_end


def test_extend_at_the_counter_from_today(page: Page, site: str) -> None:
    page.set_viewport_size(TABLET)
    sign_in(page, site, Role.LENDER)
    (loan,) = lent_since(5, 1, ("DAV-HL-G-1", "Klettergurt"))  # 1 Tag überfällig

    page.goto(f"{site}/theke/heute/")
    page.get_by_role("link", name="Kim Beispiel: verlängern").click()
    page.wait_for_load_state("load")
    assert_fits_width(page)
    new_end = timezone.localdate() + timedelta(days=3)
    page.get_by_label("Neu zurück bis").fill(new_end.isoformat())
    page.get_by_label("persönlich").check()
    page.get_by_label("Bestätigung per E-Mail an den Entleiher").uncheck()
    page.get_by_role("button", name="Verlängern").click()

    expect(page.get_by_text(f"Verlängert: 1 Teil bis {new_end:%d.%m.%Y}.")).to_be_visible()
    extension = LoanExtension.objects.get(loan=loan)
    assert (extension.reason, extension.new_end) == ("in_person", new_end)
