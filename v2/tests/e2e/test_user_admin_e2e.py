"""Ende-zu-Ende: Nutzerverwaltung im Browser (#67) – anlegen, Link, Zugang einrichten.

Jede Änderung bestätigt der Admin mit einem virtuellen YubiKey (#95).
"""

import pytest
from django.utils import timezone
from playwright.sync_api import Browser, Page, expect

from accounts.models import Passkey, Role, User
from tests.e2e.conftest import add_yubikey, sign_in

pytestmark = [pytest.mark.e2e, pytest.mark.django_db(transaction=True)]
PASSWORD = "ein-langes-Test-Passwort-42"  # gitleaks:allow – künstliches Testpasswort


def create_account(admin_page: Page, site: str, username: str, role: str) -> str:
    """Legt ein Konto über die Oberfläche an und gibt den Einrichtungslink zurück."""
    admin_page.goto(f"{site}/verwaltung/nutzer/")
    admin_page.get_by_role("link", name="Neues Konto anlegen").click()
    admin_page.get_by_label("Benutzername").fill(username)
    admin_page.get_by_label("Vorname").fill("Robin")
    admin_page.get_by_label("Nachname").fill("Exempel")
    admin_page.get_by_label("Rolle").select_option(label=role)
    admin_page.get_by_role("button", name="Anlegen").click()
    expect(
        admin_page.get_by_role("heading", name="Einrichtungslink für Robin Exempel")
    ).to_be_visible()
    return admin_page.get_by_label("Link").input_value()


def test_admin_creates_lender_who_sets_password(page: Page, site: str, browser: Browser) -> None:
    admin_context = browser.new_context()
    admin_page = admin_context.new_page()
    add_yubikey(admin_page, sign_in(admin_page, site, Role.ADMIN))

    link = create_account(admin_page, site, "robin", "Verleiher")
    page.goto(link)
    page.get_by_label("Neues Passwort", exact=True).fill(PASSWORD)
    page.get_by_label("Neues Passwort bestätigen").fill(PASSWORD)
    page.get_by_role("button", name="Passwort festlegen").click()

    expect(page.get_by_text("Passwort festgelegt. Sie sind angemeldet.")).to_be_visible()
    expect(page.get_by_text("(Verleiher)")).to_be_visible()
    page.goto(link)
    expect(page.get_by_role("heading", name="Link ungültig")).to_be_visible()
    admin_context.close()


def test_admin_creates_expert_who_enrolls_passkey(
    page: Page, site: str, browser: Browser, authenticator: str
) -> None:
    """PSA-Sachkundige: kein Passwort angeboten, Passkey über den Link; Sachkunde im Browser."""
    admin_context = browser.new_context()
    admin_page = admin_context.new_page()
    add_yubikey(admin_page, sign_in(admin_page, site, Role.ADMIN))

    link = create_account(admin_page, site, "robin", "PSA-Sachkundig")
    page.goto(link)
    expect(page.get_by_role("button", name="Passwort festlegen")).to_have_count(0)
    page.get_by_label("Bezeichnung").fill("YubiKey")
    page.get_by_role("button", name="Passkey einrichten").click()
    expect(page.get_by_text("Passkey eingerichtet. Sie sind angemeldet.")).to_be_visible()

    expert = User.objects.get(username="robin")
    admin_page.goto(f"{site}/verwaltung/nutzer/{expert.pk}/")
    today = timezone.localdate()
    admin_page.get_by_label("gültig ab").fill(today.isoformat())
    admin_page.get_by_label("gültig bis einschließlich").fill(
        today.replace(year=today.year + 1).isoformat()
    )
    admin_page.get_by_label("Nachweis (z. B. Lehrgang, Anbieter und Datum)").fill(
        "Lehrgang (Testdaten)"
    )
    admin_page.get_by_role("button", name="Eintragen").click()

    expect(admin_page.get_by_text("Sachkunde für")).to_be_visible()
    expect(admin_page.get_by_text("Zurzeit gültig.")).to_be_visible()
    assert Passkey.objects.get(user=expert).name == "YubiKey"
    assert expert.may_inspect()
    admin_context.close()


def test_promotion_ends_running_session_in_browser(page: Page, site: str, browser: Browser) -> None:
    """Nach dem Rollenwechsel muss sich die Person neu anmelden (Sitzungsstand, #67)."""
    lender = sign_in(page, site, Role.LENDER)
    page.goto(f"{site}/")
    expect(page.get_by_text("(Verleiher)")).to_be_visible()
    admin_context = browser.new_context()
    admin_page = admin_context.new_page()
    add_yubikey(admin_page, sign_in(admin_page, site, Role.ADMIN))

    admin_page.goto(f"{site}/verwaltung/nutzer/{lender.pk}/")
    admin_page.get_by_label("Neue Rolle").select_option(label="Trainer/Materialwart")
    admin_page.locator("#role-reason").fill("Materialwart")
    admin_page.get_by_role("button", name="Rolle ändern").click()
    expect(admin_page.get_by_text("Rolle von")).to_be_visible()

    page.reload()
    expect(page.get_by_role("heading", name="Anmelden")).to_be_visible()
    admin_context.close()
