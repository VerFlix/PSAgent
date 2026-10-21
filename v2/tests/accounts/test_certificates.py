"""Zertifikatsliste schützen (#18, Konzept „Zertifikatsliste schützen“).

Abnahme:

- Eine Signatur mit nicht gelistetem oder widerrufenem Zertifikat wird abgelehnt.
- Eine direkte Änderung der Liste in der Datenbank wird erkannt.
- Rechte: Aufnehmen und widerrufen nur mit „Nutzer verwalten“ (Admin), sonst abgewiesen.

Alle Zertifikate sind künstlich (`tests/accounts/yubikey.py`).
"""

from datetime import datetime, timedelta
from typing import Any

import pytest
from asn1crypto import core as asn1
from cryptography import x509
from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import models
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from accounts import certificates
from accounts.certificates import (
    OID_POLICY,
    OID_SERIAL,
    PIN_ALWAYS,
    TOUCH_ALWAYS,
    CertificateError,
)
from accounts.models import (
    QualificationRevocation,
    Role,
    SigningCertificate,
    SigningCertificateRevocation,
    User,
)
from core import chain, witness
from core.append_only import AppendOnlyError
from core.models import ChainLink, WitnessHead
from tests.accounts.conftest import SERIALS, MakeUser
from tests.accounts.yubikey import FakeYubico, FakeYubiKey, der, issue, name, new_key, pem
from tests.helpers import make_inspector, tamper
from tests.yubikey_client import login_with_yubikey

pytestmark = pytest.mark.django_db
SERIAL, OTHER_SERIAL = SERIALS


@pytest.fixture
def admin(make_user: MakeUser) -> User:
    return make_user(Role.ADMIN)


@pytest.fixture
def inspector() -> User:
    return make_inspector()


@pytest.fixture
def yubikey(yubico: FakeYubico) -> FakeYubiKey:
    return yubico.yubikey(SERIAL)


def register(
    user: User, key: FakeYubiKey, actor: User | None, name: str = "YubiKey 1"
) -> SigningCertificate:
    return certificates.register(user, **key.upload(), name=name, actor=actor)


def soon(minutes: int = 1) -> datetime:
    return timezone.now() + timedelta(minutes=minutes)


# --- Aufnehmen ---


def test_admin_registers_attested_certificate(
    admin: User, inspector: User, yubikey: FakeYubiKey
) -> None:
    entry = register(inspector, yubikey, admin)

    assert entry.user == inspector and entry.created_by == admin
    assert entry.yubikey_serial == SERIAL
    assert entry.sha256 == certificates.fingerprint(yubikey.certificate)
    assert bytes(entry.der) == der(yubikey.certificate)
    assert entry.attestation.encode() == pem(yubikey.attestation, yubikey.intermediate)
    assert ChainLink.objects.filter(table="accounts.SigningCertificate", row_pk=str(entry.pk))
    assert chain.verify() == []


def test_certificate_list_is_append_only(
    admin: User, inspector: User, yubikey: FakeYubiKey
) -> None:
    entry = register(inspector, yubikey, admin)
    certificates.revoke(entry, actor=admin, reason="YubiKey verloren")

    for model in (SigningCertificate, SigningCertificateRevocation):
        with pytest.raises(AppendOnlyError):
            model.objects.all().update(created_by=None)
        with pytest.raises(AppendOnlyError):
            model.objects.all().delete()


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ("fremde Wurzel", "nicht bis zum Wurzelzertifikat von Yubico"),
        ("ohne Zwischenzertifikat", "nicht bis zum Wurzelzertifikat von Yubico"),
        ("anderer Schlüssel", "zu einem anderen Schlüssel"),
        ("unbekannter YubiKey", "nicht zugelassen"),
        ("PIN nur einmal", "PIN und Berührung bei jeder Signatur"),
        ("ohne Berührung", "PIN und Berührung bei jeder Signatur"),
        ("Berührung zwischengespeichert", "PIN und Berührung bei jeder Signatur"),
        ("anderer Slot", "nicht für Slot 9c"),
    ],
)
def test_certificate_without_valid_attestation_is_refused(
    change: str, message: str, admin: User, inspector: User, yubico: FakeYubico
) -> None:
    key = yubico.yubikey(SERIAL)
    upload = key.upload()
    if change == "fremde Wurzel":
        upload = FakeYubico().yubikey(SERIAL).upload()
    elif change == "ohne Zwischenzertifikat":
        upload["intermediates"] = b""
    elif change == "anderer Schlüssel":
        upload["certificate"] = yubico.yubikey(SERIAL).upload()["certificate"]
    elif change == "unbekannter YubiKey":
        upload = yubico.yubikey(4711).upload()
    elif change == "PIN nur einmal":
        upload = yubico.yubikey(SERIAL, pin=2).upload()
    elif change == "ohne Berührung":
        upload = yubico.yubikey(SERIAL, touch=1).upload()
    elif change == "Berührung zwischengespeichert":
        upload = yubico.yubikey(SERIAL, touch=3).upload()
    elif change == "anderer Slot":
        upload = yubico.yubikey(SERIAL, slot="9a").upload()

    with pytest.raises(CertificateError, match=message):
        certificates.register(inspector, **upload, name="YubiKey 1", actor=admin)

    assert not SigningCertificate.objects.exists()


def test_slot_attestation_cannot_vouch_for_another_key(
    admin: User, inspector: User, yubico: FakeYubico
) -> None:
    """Angriff: Mit dem Schlüssel aus Slot 9a des eigenen YubiKeys eine „Bescheinigung 9c“
    mit fremder, zugelassener Seriennummer für einen Schlüssel außerhalb des YubiKeys signieren."""
    own = yubico.yubikey(4711, slot="9a")
    software_key = new_key()
    forged = issue(
        name("YubiKey PIV Attestation 9c"),
        software_key.public_key(),
        own.attestation.subject,
        own.key,
        extensions=(
            x509.UnrecognizedExtension(OID_SERIAL, asn1.Integer(SERIAL).dump()),
            x509.UnrecognizedExtension(OID_POLICY, bytes([PIN_ALWAYS, TOUCH_ALWAYS])),
        ),
    )
    certificate = issue(
        name("Angreifer"), software_key.public_key(), name("Angreifer"), software_key
    )

    with pytest.raises(CertificateError, match="nicht bis zum Wurzelzertifikat von Yubico"):
        certificates.register(
            inspector,
            certificate=der(certificate),
            attestation=pem(forged),
            intermediates=pem(own.attestation, own.intermediate),
            name="untergeschoben",
            actor=admin,
        )


def test_end_entity_certificate_cannot_be_an_intermediate(
    admin: User, inspector: User, yubico: FakeYubico
) -> None:
    key = yubico.yubikey(SERIAL)
    device_key = new_key()
    end_entity = issue(
        name("Yubico PIV Attestation"),
        device_key.public_key(),
        yubico.root.subject,
        yubico.key,
        extensions=(x509.BasicConstraints(ca=False, path_length=None),),
    )
    attestation = issue(
        key.attestation.subject,
        key.key.public_key(),
        end_entity.subject,
        device_key,
        extensions=tuple(e.value for e in key.attestation.extensions),
    )

    with pytest.raises(CertificateError, match="nicht bis zum Wurzelzertifikat von Yubico"):
        certificates.register(
            inspector,
            certificate=der(key.certificate),
            attestation=pem(attestation),
            intermediates=pem(end_entity),
            name="YubiKey 1",
            actor=admin,
        )


def test_device_certificate_without_basic_constraints_is_accepted(
    admin: User, inspector: User, yubico: FakeYubico
) -> None:
    """Ältere Gerätezertifikate (Slot f9) tragen keine BasicConstraints; als direkter
    Aussteller der Bescheinigung gelten sie trotzdem."""
    entry = register(
        inspector, yubico.yubikey(SERIAL, device_ca=False, device_serial=SERIAL), admin
    )

    assert entry.yubikey_serial == SERIAL


def test_device_certificate_of_another_yubikey_is_refused(
    admin: User, inspector: User, yubico: FakeYubico
) -> None:
    key = yubico.yubikey(SERIAL, device_ca=False, device_serial=OTHER_SERIAL)

    with pytest.raises(CertificateError, match="nicht bis zum Wurzelzertifikat von Yubico"):
        register(inspector, key, admin)


def test_non_ca_certificate_higher_up_is_refused(
    admin: User, inspector: User, yubico: FakeYubico
) -> None:
    """Über dem Gerätezertifikat zählt nur, was ausdrücklich Zertifizierungsstelle ist."""
    middle_key, device_key, key = new_key(), new_key(), yubico.yubikey(SERIAL)
    middle = issue(
        name("Zwischenstelle ohne Kennzeichnung"),
        middle_key.public_key(),
        yubico.root.subject,
        yubico.key,
    )
    device = issue(
        name("Yubico PIV Attestation"), device_key.public_key(), middle.subject, middle_key, ca=True
    )
    attestation = issue(
        key.attestation.subject,
        key.key.public_key(),
        device.subject,
        device_key,
        extensions=tuple(e.value for e in key.attestation.extensions),
    )

    with pytest.raises(CertificateError, match="nicht bis zum Wurzelzertifikat von Yubico"):
        certificates.register(
            inspector,
            certificate=der(key.certificate),
            attestation=pem(attestation),
            intermediates=pem(device, middle),
            name="YubiKey 1",
            actor=admin,
        )


def test_unreadable_upload_is_refused(admin: User, inspector: User, yubikey: FakeYubiKey) -> None:
    upload = yubikey.upload() | {"certificate": b"kein Zertifikat"}

    with pytest.raises(CertificateError, match="kein lesbares Zertifikat"):
        certificates.register(inspector, **upload, name="YubiKey 1", actor=admin)


def test_expired_certificate_is_refused(admin: User, inspector: User, yubico: FakeYubico) -> None:
    old = yubico.yubikey(SERIAL, valid_from=timezone.now() - timedelta(days=400), days=365)

    with pytest.raises(CertificateError, match="abgelaufen"):
        register(inspector, old, admin)


def test_same_certificate_or_key_only_once(
    admin: User, inspector: User, yubico: FakeYubico, yubikey: FakeYubiKey
) -> None:
    entry = register(inspector, yubikey, admin)
    certificates.revoke(entry, actor=admin, reason="YubiKey verloren")

    with pytest.raises(CertificateError, match="Zertifikat steht schon"):
        register(inspector, yubikey, admin, name="nochmal")
    # Neues Zertifikat für den widerrufenen Schlüssel: bleibt gesperrt.
    renewed = yubico.yubikey(SERIAL, key=yubikey.key)
    with pytest.raises(CertificateError, match="Schlüssel steht schon"):
        register(inspector, renewed, admin, name="erneuert")


def test_certificates_only_for_ppe_experts(
    admin: User, make_user: MakeUser, yubikey: FakeYubiKey
) -> None:
    trainer = make_user(Role.TRAINER)

    with pytest.raises(CertificateError, match="nur für PSA-Sachkundige"):
        register(trainer, yubikey, admin)


def test_missing_settings_are_named(
    settings: Any, admin: User, inspector: User, yubikey: FakeYubiKey
) -> None:
    settings.PSAGENT_YUBIKEY_SERIALS = []
    with pytest.raises(CertificateError, match="PSAGENT_YUBIKEY_SERIALS"):
        register(inspector, yubikey, admin)

    settings.PSAGENT_YUBICO_CA_PATH = ""
    with pytest.raises(CertificateError, match="PSAGENT_YUBICO_CA_PATH"):
        register(inspector, yubikey, admin)


# --- Widerrufen ---


def test_revocation_needs_reason_and_happens_once(
    admin: User, inspector: User, yubikey: FakeYubiKey
) -> None:
    entry = register(inspector, yubikey, admin)

    with pytest.raises(CertificateError, match="Begründung"):
        certificates.revoke(entry, actor=admin, reason="  ")
    revocation = certificates.revoke(entry, actor=admin, reason="YubiKey verloren")
    with pytest.raises(CertificateError, match="bereits widerrufen"):
        certificates.revoke(entry, actor=admin, reason="nochmal")

    assert revocation.created_by == admin and revocation.revoked_at <= timezone.now()


def test_revocation_may_be_backdated_but_not_in_the_future(
    admin: User, inspector: User, yubikey: FakeYubiKey
) -> None:
    entry = register(inspector, yubikey, admin)

    with pytest.raises(CertificateError, match="ab jetzt oder ab einem früheren"):
        certificates.revoke(entry, actor=admin, reason="Test", revoked_at=soon(60))
    lost = timezone.now() - timedelta(days=3)
    revocation = certificates.revoke(
        entry, actor=admin, reason="YubiKey seit dem Kurs verloren", revoked_at=lost
    )

    assert revocation.revoked_at == lost


# --- Signaturen gegen den Stand zum Signaturzeitpunkt prüfen ---


def test_listed_certificate_with_qualification_is_accepted(
    admin: User, inspector: User, yubikey: FakeYubiKey
) -> None:
    entry = register(inspector, yubikey, admin)

    assert certificates.signer_at(der(yubikey.certificate), soon()) == entry
    assert certificates.signer_at(yubikey.certificate, soon()) == entry


def test_unlisted_certificate_is_refused(
    admin: User, inspector: User, yubico: FakeYubico, yubikey: FakeYubiKey
) -> None:
    """Abnahme: Eine Signatur mit nicht gelistetem Zertifikat wird abgelehnt."""
    register(inspector, yubikey, admin)
    unlisted = yubico.yubikey(OTHER_SERIAL)

    with pytest.raises(CertificateError, match="steht nicht in der Liste"):
        certificates.signer_at(unlisted.certificate, soon())


def test_revoked_certificate_is_refused_from_revocation_on(
    admin: User, inspector: User, yubikey: FakeYubiKey
) -> None:
    """Abnahme: Signatur mit widerrufenem Zertifikat abgelehnt; frühere bleiben gültig."""
    entry = register(inspector, yubikey, admin)
    signed_before = timezone.now()
    revocation = certificates.revoke(entry, actor=admin, reason="YubiKey verloren")

    assert certificates.signer_at(yubikey.certificate, signed_before) == entry
    for moment in (revocation.revoked_at, soon()):
        with pytest.raises(CertificateError, match="widerrufen"):
            certificates.signer_at(yubikey.certificate, moment)


def test_backdated_revocation_applies_from_its_date(
    admin: User, inspector: User, yubikey: FakeYubiKey
) -> None:
    entry = register(inspector, yubikey, admin)
    certificates.revoke(entry, actor=admin, reason="verloren", revoked_at=entry.created_at)

    with pytest.raises(CertificateError, match="widerrufen"):
        certificates.signer_at(yubikey.certificate, entry.created_at + timedelta(seconds=1))


def test_signature_before_listing_is_refused(
    admin: User, inspector: User, yubikey: FakeYubiKey
) -> None:
    entry = register(inspector, yubikey, admin)

    with pytest.raises(CertificateError, match="noch nicht hinterlegt"):
        certificates.signer_at(yubikey.certificate, entry.created_at - timedelta(seconds=1))


def test_certificate_counts_only_with_valid_qualification(
    admin: User, inspector: User, yubikey: FakeYubiKey
) -> None:
    """Ein Zertifikat gilt nur zusammen mit gültiger Sachkunde zum Signaturzeitpunkt."""
    register(inspector, yubikey, admin)
    before = timezone.now()
    qualification = inspector.qualifications.get()
    QualificationRevocation.objects.create(qualification=qualification, reason="Test")

    assert certificates.signer_at(yubikey.certificate, before).user == inspector
    with pytest.raises(CertificateError, match="keine gültige Sachkunde"):
        certificates.signer_at(yubikey.certificate, soon())


def test_certificate_of_former_ppe_expert_is_refused(
    admin: User, inspector: User, yubikey: FakeYubiKey
) -> None:
    register(inspector, yubikey, admin)
    User.objects.filter(pk=inspector.pk).update(role=Role.TRAINER)

    with pytest.raises(CertificateError, match="nicht \\(mehr\\) PSA-Sachkundig"):
        certificates.signer_at(yubikey.certificate, soon())


def test_yubikey_removed_from_settings_is_refused(
    settings: Any, admin: User, inspector: User, yubikey: FakeYubiKey
) -> None:
    register(inspector, yubikey, admin)
    settings.PSAGENT_YUBIKEY_SERIALS = [str(OTHER_SERIAL)]

    with pytest.raises(CertificateError, match="nicht zugelassen"):
        certificates.signer_at(yubikey.certificate, soon())


# --- Direkte Änderungen in der Datenbank ---


def test_changed_owner_in_database_is_detected(
    admin: User, inspector: User, yubikey: FakeYubiKey
) -> None:
    """Abnahme: Wer das Zertifikat per SQL einer anderen Person zuordnet, fällt auf."""
    entry = register(inspector, yubikey, admin)
    other = make_inspector()

    tamper(
        "accounts_signingcertificate",
        "UPDATE accounts_signingcertificate SET user_id = %s WHERE id = %s",
        [other.pk, entry.pk],
    )

    assert any("Vorgang wurde geändert" in p for p in certificates.list_problems())
    assert any("accounts.SigningCertificate" in p for p in chain.verify())
    with pytest.raises(CertificateError, match="an der App vorbei geändert"):
        certificates.signer_at(yubikey.certificate, soon())


def test_deleted_revocation_in_database_is_detected(
    admin: User, inspector: User, yubikey: FakeYubiKey
) -> None:
    """Abnahme: Ein per SQL gelöschter Widerruf macht das Zertifikat nicht wieder gültig."""
    entry = register(inspector, yubikey, admin)
    certificates.revoke(entry, actor=admin, reason="YubiKey verloren")

    tamper(
        "accounts_signingcertificaterevocation",
        "DELETE FROM accounts_signingcertificaterevocation WHERE certificate_id = %s",
        [entry.pk],
    )

    assert any("Vorgang wurde gelöscht" in p for p in certificates.list_problems())
    assert any("Vorgang wurde gelöscht" in p for p in chain.verify())
    with pytest.raises(CertificateError, match="an der App vorbei geändert"):
        certificates.signer_at(yubikey.certificate, soon())


def _rewrite(entry: SigningCertificate, owner: User) -> None:
    """Ändert die Zeile und rechnet ihr Glied passend neu, wie ein Angreifer mit Datenbankzugang."""
    tamper(
        "accounts_signingcertificate",
        "UPDATE accounts_signingcertificate SET user_id = %s WHERE id = %s",
        [owner.pk, entry.pk],
    )
    link = ChainLink.objects.get(table="accounts.SigningCertificate", row_pk=str(entry.pk))
    row = SigningCertificate.objects.filter(pk=entry.pk).values(*link.fields).get()
    payload = chain.payload_digest(link.table, link.row_pk, row)
    digest = chain.link_digest(
        seq=link.seq,
        prev=link.prev_hash,
        table=link.table,
        pk=link.row_pk,
        fields=list(link.fields),
        payload=payload,
        at=link.at,
    )
    tamper(
        "core_chainlink",
        "UPDATE core_chainlink SET payload_sha256 = %s, hash = %s WHERE id = %s",
        [payload, digest, link.pk],
    )


@pytest.mark.parametrize("later", ["weitere Vorgänge", "nur Kettenkopf"])
def test_recomputed_link_is_detected(
    later: str, admin: User, inspector: User, yubikey: FakeYubiKey
) -> None:
    """Auch ein passend neu berechnetes Glied fällt auf: am Nachfolger oder am Kettenkopf."""
    entry = register(inspector, yubikey, admin)
    if later == "weitere Vorgänge":
        make_inspector()  # legt Sachkunde an, also ein neues Glied hinter dem Zertifikat
    else:
        head = chain.head()
        assert head is not None
        WitnessHead.objects.create(seq=head.seq, head_hash=head.hash)
    _rewrite(entry, make_inspector())

    assert certificates.list_problems()
    with pytest.raises(CertificateError, match="an der App vorbei geändert"):
        certificates.signer_at(yubikey.certificate, soon())


def test_row_inserted_past_the_app_is_detected(
    admin: User, inspector: User, yubico: FakeYubico, yubikey: FakeYubiKey
) -> None:
    """Abnahme: Eine an der App vorbei eingefügte Zeile (ohne Kettenglied) fällt auf."""
    register(inspector, yubikey, admin)
    foreign = yubico.yubikey(OTHER_SERIAL)
    row = SigningCertificate(
        user=inspector,
        name="untergeschoben",
        der=der(foreign.certificate),
        sha256=certificates.fingerprint(foreign.certificate),
        key_sha256=certificates.key_fingerprint(foreign.certificate),
        subject="CN=untergeschoben",
        not_valid_before=foreign.certificate.not_valid_before_utc,
        not_valid_after=foreign.certificate.not_valid_after_utc,
        yubikey_serial=OTHER_SERIAL,
        attestation=pem(foreign.attestation, foreign.intermediate).decode(),
    )
    models.Model.save(row, force_insert=True)  # wie ein INSERT per SQL: kein Kettenglied

    assert any("ohne Kettenglied" in p for p in certificates.list_problems())
    assert any("ohne Kettenglied" in p for p in chain.verify())
    with pytest.raises(CertificateError, match="an der App vorbei geändert"):
        certificates.signer_at(foreign.certificate, soon())


def test_inserted_row_without_attestation_is_refused(
    admin: User, inspector: User, yubico: FakeYubico
) -> None:
    """Auch eine sauber verkettete Zeile hilft nicht ohne echte Herstellerbescheinigung."""
    stranger = FakeYubico().yubikey(SERIAL)
    SigningCertificate.objects.create(
        user=inspector,
        name="untergeschoben",
        der=der(stranger.certificate),
        sha256=certificates.fingerprint(stranger.certificate),
        key_sha256=certificates.key_fingerprint(stranger.certificate),
        subject="CN=untergeschoben",
        not_valid_before=stranger.certificate.not_valid_before_utc,
        not_valid_after=stranger.certificate.not_valid_after_utc,
        yubikey_serial=SERIAL,
        attestation=pem(stranger.attestation, stranger.intermediate).decode(),
    )

    with pytest.raises(CertificateError, match="Wurzelzertifikat von Yubico"):
        certificates.signer_at(stranger.certificate, soon())


# --- Rechte ---


@pytest.mark.parametrize("role", [Role.PPE_EXPERT, Role.TRAINER, Role.LENDER, Role.VIEWER])
def test_only_admins_register_or_revoke(
    role: Role, admin: User, inspector: User, yubico: FakeYubico, make_user: MakeUser
) -> None:
    """Rechte: erlaubt mit „Nutzer verwalten“ (Admin), verboten für alle anderen Rollen."""
    actor = make_inspector(role=role) if role == Role.PPE_EXPERT else make_user(role)
    entry = register(inspector, yubico.yubikey(SERIAL), admin)

    with pytest.raises(PermissionDenied):
        register(inspector, yubico.yubikey(OTHER_SERIAL), actor)
    with pytest.raises(PermissionDenied):
        certificates.revoke(entry, actor=actor, reason="Test")

    assert SigningCertificate.objects.count() == 1
    assert not SigningCertificateRevocation.objects.exists()


def test_admin_uses_the_list_in_user_management(
    client: Client, admin: User, inspector: User, yubikey: FakeYubiKey
) -> None:
    login_with_yubikey(client, admin)
    url = reverse("accounts:certificate_register", args=[inspector.pk])
    upload = yubikey.upload()

    response = client.post(
        url,
        {
            "name": "YubiKey 1",
            "certificate": _file("zertifikat.der", upload["certificate"]),
            "attestation": _file("attest-9c.pem", upload["attestation"]),
            "intermediates": _file("f9.pem", upload["intermediates"]),
        },
        follow=True,
    )

    entry = SigningCertificate.objects.get()
    page = response.content.decode()
    assert "Signaturzertifikat „YubiKey 1“ aufgenommen" in page
    assert entry.sha256 in page

    response = client.post(
        reverse("accounts:certificate_revoke", args=[inspector.pk, entry.pk]),
        {"reason": "YubiKey verloren", "revoked_at": ""},
        follow=True,
    )

    assert "widerrufen" in response.content.decode()
    assert SigningCertificateRevocation.objects.get().reason == "YubiKey verloren"


def test_refused_upload_shows_reason(
    client: Client, admin: User, inspector: User, yubico: FakeYubico
) -> None:
    login_with_yubikey(client, admin)
    upload = yubico.yubikey(SERIAL, touch=TOUCH_ALWAYS + 1).upload()

    response = client.post(
        reverse("accounts:certificate_register", args=[inspector.pk]),
        {
            "name": "YubiKey 1",
            "certificate": _file("zertifikat.der", upload["certificate"]),
            "attestation": _file("attest-9c.pem", upload["attestation"]),
            "intermediates": _file("f9.pem", upload["intermediates"]),
        },
        follow=True,
    )

    assert "PIN und Berührung bei jeder Signatur" in response.content.decode()
    assert not SigningCertificate.objects.exists()


def _file(name: str, content: bytes) -> SimpleUploadedFile:
    return SimpleUploadedFile(name, content)


# --- Zeuge ---


def test_witness_report_lists_fingerprints_without_names(
    admin: User, inspector: User, yubikey: FakeYubiKey
) -> None:
    entry = register(inspector, yubikey, admin)
    certificates.revoke(entry, actor=admin, reason="YubiKey verloren")
    head = chain.head()
    assert head is not None

    text = witness.report(WitnessHead.objects.create(seq=head.seq, head_hash=head.hash))

    assert entry.sha256 in text and "widerrufen ab" in text
    assert inspector.last_name not in text and "Testperson" not in text
