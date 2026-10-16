"""Ende-zu-Ende: Seiten passen auf Handy und Tablet (#13, mobile Ansicht).

Jede bestehende Seite einmal je Geräteprofil: Überschrift sichtbar, nichts steht seitlich über.
Verleih und Prüfmodus kommen mit ihren Oberflächen dazu.
"""

import pytest
from django.utils import timezone
from playwright.sync_api import Page, expect

from accounts.models import Passkey, Role
from accounts.services import create_account, grant_qualification
from tests.e2e.conftest import assert_fits_width, sign_in

pytestmark = [pytest.mark.e2e, pytest.mark.django_db(transaction=True)]


def test_login_page(device_page: Page, site: str) -> None:
    device_page.goto(f"{site}/anmelden/")

    expect(device_page.get_by_role("heading", name="Anmelden")).to_be_visible()
    assert_fits_width(device_page)


def test_enrollment_page(device_page: Page, site: str) -> None:
    _, token = create_account(
        "robin", first_name="Robin", last_name="Exempel", role=Role.LENDER, actor=None
    )

    device_page.goto(f"{site}/einrichten/{token}/")

    expect(device_page.get_by_role("heading", name="Zugang einrichten")).to_be_visible()
    assert_fits_width(device_page)


def test_home_and_account(device_page: Page, site: str) -> None:
    user = sign_in(device_page, site, Role.LENDER)
    Passkey.objects.create(
        user=user, name="YubiKey mit langem Namen", credential_id=b"m1", public_key=b"\x00"
    )

    for path, heading in [("/", "Willkommen"), ("/konto/", "Konto")]:
        device_page.goto(f"{site}{path}")
        expect(device_page.get_by_role("heading", name=heading).first).to_be_visible()
        assert_fits_width(device_page)


def test_user_management(device_page: Page, site: str) -> None:
    admin = sign_in(device_page, site, Role.ADMIN)
    user, _ = create_account(
        "robin", first_name="Robin", last_name="Exempel", role=Role.PPE_EXPERT, actor=admin
    )
    today = timezone.localdate()
    grant_qualification(
        user,
        valid_from=today,
        valid_until=today,
        evidence="Lehrgang PSA-Sachkunde, Beispielanbieter, Testdaten",
        actor=admin,
    )
    Passkey.objects.create(user=user, name="YubiKey", credential_id=b"m2", public_key=b"\x00")

    pages = [
        ("/verwaltung/nutzer/", "Nutzer verwalten"),
        ("/verwaltung/nutzer/neu/", "Konto anlegen"),
        (f"/verwaltung/nutzer/{user.pk}/", "Robin Exempel"),
    ]
    for path, heading in pages:
        device_page.goto(f"{site}{path}")
        expect(device_page.get_by_role("heading", name=heading, exact=True)).to_be_visible()
        assert_fits_width(device_page)
