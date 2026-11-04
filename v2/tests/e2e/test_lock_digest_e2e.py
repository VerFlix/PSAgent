"""Ende-zu-Ende: Sammelmail zu neuen Sperren einstellen (#141).

Der Admin trägt Empfänger ein und bestätigt mit einem virtuellen YubiKey; danach Testmail.
"""

import pytest
from django.core import mail
from playwright.sync_api import Page, expect

from accounts.models import Role
from inventory.models import LockDigestSetting
from tests.e2e.conftest import add_yubikey, sign_in

pytestmark = [pytest.mark.e2e, pytest.mark.django_db(transaction=True)]


def test_admin_sets_lock_digest_recipients_with_yubikey(page: Page, site: str) -> None:
    add_yubikey(page, sign_in(page, site, Role.ADMIN))
    page.goto(f"{site}/teile/")
    page.get_by_role("link", name="Sammelmail zu neuen Sperren einstellen").click()

    page.get_by_label("Sammelmail zu Sperren senden").check()
    page.get_by_label("Empfänger, eine Adresse je Zeile").fill("psa@example.org")
    page.get_by_role("button", name="Speichern (mit YubiKey)").click()

    expect(page.get_by_text("Sammelmail zu Sperren an, Empfänger gespeichert.")).to_be_visible()
    setting = LockDigestSetting.objects.get()
    assert setting.enabled and setting.recipients == ["psa@example.org"]

    page.get_by_role("button", name="Testmail senden").click()

    expect(page.get_by_text("Testmail an 1 Adresse(n) gesendet.")).to_be_visible()
    assert [message.to for message in mail.outbox] == [["psa@example.org"]]
