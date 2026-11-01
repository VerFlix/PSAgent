"""Ende-zu-Ende: Sammelmail zu überfälligen Ausleihen einstellen (#128).

Der Admin trägt Empfänger ein und bestätigt mit einem virtuellen YubiKey; danach Testmail.
"""

import pytest
from django.core import mail
from playwright.sync_api import Page, expect

from accounts.models import Role
from lending.models import DigestSetting
from tests.e2e.conftest import add_yubikey, sign_in

pytestmark = [pytest.mark.e2e, pytest.mark.django_db(transaction=True)]


def test_admin_sets_recipients_with_yubikey_and_sends_test_mail(page: Page, site: str) -> None:
    add_yubikey(page, sign_in(page, site, Role.ADMIN))
    page.goto(f"{site}/verleih/")
    page.get_by_role("link", name="Sammelmail an Vereinsadressen einstellen").click()

    page.get_by_label("Sammelmail senden").check()
    page.get_by_label("Empfänger, eine Adresse je Zeile").fill(
        "material@example.org\nvorstand@example.org"
    )
    page.get_by_role("button", name="Speichern (mit YubiKey)").click()

    expect(page.get_by_text("Sammelmail an, Empfänger gespeichert.")).to_be_visible()
    setting = DigestSetting.objects.get()
    assert setting.enabled
    assert setting.recipients == ["material@example.org", "vorstand@example.org"]

    page.get_by_role("button", name="Testmail senden").click()

    expect(page.get_by_text("Testmail an 2 Adresse(n) gesendet.")).to_be_visible()
    assert [message.to for message in mail.outbox] == [
        ["material@example.org"],
        ["vorstand@example.org"],
    ]
