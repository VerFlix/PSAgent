"""Ende-zu-Ende: Anmeldung an der Theke mit QR-Code und PIN im Tablet-Format (#126)."""

import pytest
from django.conf import settings
from django.http import HttpResponse
from playwright.sync_api import Page, expect

from accounts import counter
from accounts.devices import register_device
from accounts.models import Role, User
from tests.e2e.conftest import assert_fits_width

pytestmark = [pytest.mark.e2e, pytest.mark.django_db(transaction=True)]
PIN = "482915"


def test_lender_logs_in_at_the_counter(page: Page, site: str) -> None:
    admin = User.objects.create_user(username="e2e-admin", role=Role.ADMIN)
    lender = User.objects.create_user(
        username="e2e-verleih", first_name="Testperson", last_name="Verleih", role=Role.LENDER
    )
    counter.set_pin(lender, PIN, actor=lender)
    code = counter.issue_qr(lender, actor=lender)
    response = HttpResponse()
    register_device("Tablet Ausgabe", actor=admin, response=response, counter=True)
    cookie = response.cookies[settings.PSAGENT_CLUB_DEVICE_COOKIE].value
    page.context.add_cookies(
        [{"name": settings.PSAGENT_CLUB_DEVICE_COOKIE, "value": cookie, "url": site}]
    )
    page.set_viewport_size({"width": 1280, "height": 800})

    page.goto(f"{site}/anmelden/")
    assert_fits_width(page)
    page.get_by_label("Persönlicher QR-Code").fill(code)
    page.get_by_label("PIN", exact=True).fill("111999")
    page.get_by_role("button", name="Anmelden").first.click()
    expect(page.get_by_text("Falsche PIN. Noch 4 Versuche.")).to_be_visible()

    page.get_by_label("Persönlicher QR-Code").fill(code)
    page.get_by_label("PIN", exact=True).fill(PIN)
    page.get_by_role("button", name="Anmelden").first.click()
    expect(page.get_by_role("heading", name="Willkommen, Testperson Verleih")).to_be_visible()
