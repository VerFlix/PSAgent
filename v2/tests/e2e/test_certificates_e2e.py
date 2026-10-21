"""Ende-zu-Ende: Signaturzertifikate in der Nutzerverwaltung (#18, #95).

Der Admin lädt Zertifikat, Herstellerbescheinigung und Zwischenzertifikat eines künstlichen
YubiKeys hoch und widerruft es wieder; jede Änderung bestätigt er mit einem virtuellen YubiKey.
"""

from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import Page, expect

from accounts.models import Role, SigningCertificate, SigningCertificateRevocation
from tests.accounts.yubikey import FakeYubico, der, pem
from tests.e2e.conftest import add_yubikey, sign_in
from tests.helpers import make_inspector

pytestmark = [pytest.mark.e2e, pytest.mark.django_db(transaction=True)]
SERIAL = 1000001


def test_admin_registers_and_revokes_certificate(
    page: Page, site: str, tmp_path: Path, settings: Any
) -> None:
    yubico = FakeYubico()
    (tmp_path / "yubico.pem").write_bytes(pem(yubico.root))
    settings.PSAGENT_YUBICO_CA_PATH = str(tmp_path / "yubico.pem")
    settings.PSAGENT_YUBIKEY_SERIALS = [str(SERIAL)]
    key = yubico.yubikey(SERIAL)
    files = {
        "zertifikat.der": der(key.certificate),
        "bescheinigung-9c.pem": pem(key.attestation),
        "zwischenzertifikat-f9.pem": pem(key.intermediate),
    }
    for name, content in files.items():
        (tmp_path / name).write_bytes(content)
    expert = make_inspector()
    add_yubikey(page, sign_in(page, site, Role.ADMIN))

    page.goto(f"{site}/verwaltung/nutzer/{expert.pk}/")
    page.get_by_label("Bezeichnung (z. B. YubiKey 1, Reserve)").fill("YubiKey 1")
    page.locator("#certificate-file").set_input_files(tmp_path / "zertifikat.der")
    page.locator("#certificate-attestation").set_input_files(tmp_path / "bescheinigung-9c.pem")
    page.locator("#certificate-intermediates").set_input_files(
        tmp_path / "zwischenzertifikat-f9.pem"
    )
    page.get_by_role("button", name="Aufnehmen").click()

    expect(page.get_by_text("Signaturzertifikat „YubiKey 1“ aufgenommen.")).to_be_visible()
    entry = SigningCertificate.objects.get()
    expect(page.get_by_text(entry.sha256)).to_be_visible()

    page.locator(f"#certificate-reason-{entry.pk}").fill("YubiKey verloren")
    page.locator(f"#certificate-reason-{entry.pk}").locator("xpath=..").get_by_role(
        "button", name="Widerrufen"
    ).click()

    expect(page.get_by_text("Signaturzertifikat „YubiKey 1“ widerrufen.")).to_be_visible()
    expect(page.get_by_text("widerrufen ab")).to_be_visible()
    assert SigningCertificateRevocation.objects.get().reason == "YubiKey verloren"


def test_refused_certificate_shows_reason(
    page: Page, site: str, tmp_path: Path, settings: Any
) -> None:
    yubico = FakeYubico()
    (tmp_path / "yubico.pem").write_bytes(pem(yubico.root))
    settings.PSAGENT_YUBICO_CA_PATH = str(tmp_path / "yubico.pem")
    settings.PSAGENT_YUBIKEY_SERIALS = [str(SERIAL)]
    key = yubico.yubikey(4711)  # nicht zugelassen
    (tmp_path / "z.der").write_bytes(der(key.certificate))
    (tmp_path / "a.pem").write_bytes(pem(key.attestation))
    (tmp_path / "f9.pem").write_bytes(pem(key.intermediate))
    expert = make_inspector()
    add_yubikey(page, sign_in(page, site, Role.ADMIN))

    page.goto(f"{site}/verwaltung/nutzer/{expert.pk}/")
    page.get_by_label("Bezeichnung (z. B. YubiKey 1, Reserve)").fill("YubiKey fremd")
    page.locator("#certificate-file").set_input_files(tmp_path / "z.der")
    page.locator("#certificate-attestation").set_input_files(tmp_path / "a.pem")
    page.locator("#certificate-intermediates").set_input_files(tmp_path / "f9.pem")
    page.get_by_role("button", name="Aufnehmen").click()

    expect(page.get_by_text("Der YubiKey 4711 ist nicht zugelassen.")).to_be_visible()
    assert not SigningCertificate.objects.exists()


def test_admin_without_yubikey_is_told_what_is_missing(page: Page, site: str) -> None:
    """Ohne YubiKey-Passkey geht in der Oberfläche keine Änderung; der Hinweis sagt, warum."""
    expert = make_inspector()
    sign_in(page, site, Role.ADMIN)

    page.goto(f"{site}/verwaltung/nutzer/{expert.pk}/")
    page.locator("#stamp-text").fill("Testperson Prüfer (Testdaten)")
    page.get_by_role("button", name="Speichern").click()

    expect(page.locator("#yubikey-status")).to_contain_text("braucht dieses Konto einen YubiKey")
    expert.refresh_from_db()
    assert expert.stamp_text == ""
