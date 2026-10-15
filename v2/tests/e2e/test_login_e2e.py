"""Ende-zu-Ende: Anmeldung im Browser mit Passwort und Passkey (#11)."""

import re

import pytest
from playwright.sync_api import Page, expect

from accounts.enrollment import create_token
from accounts.models import Passkey, Role, User

pytestmark = [pytest.mark.e2e, pytest.mark.django_db(transaction=True)]
PASSWORD = "ein-langes-Test-Passwort-42"


def logout(page: Page) -> None:
    page.get_by_role("button", name="Abmelden").click()
    expect(page.get_by_role("heading", name="Anmelden")).to_be_visible()


def passkey_login(page: Page, site: str) -> None:
    page.goto(f"{site}/anmelden/")
    page.get_by_role("button", name="Mit Passkey anmelden").click()
    expect(page.get_by_role("heading", name=re.compile("Willkommen"))).to_be_visible()


def test_password_then_passkey(page: Page, site: str, authenticator: str) -> None:
    user = User.objects.create_user(
        username="kim", password=PASSWORD, first_name="Kim", last_name="Beispiel", role=Role.LENDER
    )

    page.goto(f"{site}/anmelden/")
    page.get_by_label("Benutzername").fill("kim")
    page.get_by_label("Passwort").fill(PASSWORD)
    page.get_by_role("button", name="Anmelden", exact=True).click()
    expect(page.get_by_role("heading", name="Willkommen, Kim Beispiel")).to_be_visible()

    page.goto(f"{site}/konto/")
    page.get_by_label("Neuer Passkey, Bezeichnung").fill("Virtueller Schlüssel")
    page.get_by_role("button", name="Passkey hinzufügen").click()
    expect(page.get_by_text("Passkey eingerichtet.", exact=True)).to_be_visible()
    assert Passkey.objects.get(user=user).name == "Virtueller Schlüssel"

    logout(page)
    passkey_login(page, site)


def test_wrong_password_in_browser(page: Page, site: str) -> None:
    User.objects.create_user(username="kim", password=PASSWORD, role=Role.LENDER)

    page.goto(f"{site}/anmelden/")
    page.get_by_label("Benutzername").fill("kim")
    page.get_by_label("Passwort").fill("falsch")
    page.get_by_role("button", name="Anmelden", exact=True).click()

    expect(page.get_by_role("alert")).to_contain_text("Benutzername oder Passwort ist falsch")


def test_admin_enrolls_and_signs_in_with_passkey(page: Page, site: str, authenticator: str) -> None:
    admin = User.objects.create_user(username="admin-test", role=Role.ADMIN, first_name="Alex")
    token = create_token(admin)

    page.goto(f"{site}/einrichten/{token}/")
    page.get_by_label("Bezeichnung").fill("YubiKey")
    page.get_by_role("button", name="Passkey einrichten").click()
    expect(page.get_by_text("Passkey eingerichtet. Sie sind angemeldet.")).to_be_visible()

    logout(page)
    passkey_login(page, site)
    expect(page.get_by_text("(Admin)")).to_be_visible()
