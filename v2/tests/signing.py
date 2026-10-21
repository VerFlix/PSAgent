"""Prüfungsrunden in Tests signieren wie mit dem YubiKey (#38). Kein echter Schlüssel, kein Netz.

`sign(round_, actor=…)` signiert das gespeicherte Prüfprotokoll mit einem künstlichen YubiKey des
Prüfers (Zertifikat samt Herstellerbescheinigung in der Zertifikatsliste) und reicht es bei
`services.sign_round` ein. Zeitstempel kommen von einem eigenen Testdienst. Die künstliche
Yubico-Wurzel und der Testdienst entstehen einmal je Testlauf.
"""

import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from functools import cache
from io import BytesIO
from pathlib import Path
from unittest import mock

from asn1crypto import keys as asn1_keys
from asn1crypto import x509 as asn1_x509
from cryptography.hazmat.primitives import serialization
from django.test import override_settings
from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
from pyhanko.sign import signers
from pyhanko_certvalidator.registry import SimpleCertificateStore

from accounts import certificates
from accounts.models import User
from inspections import services
from inspections.models import InspectionRound, RoundEvent, RoundProtocol
from inspections.protocol import SIGNATURE_FIELD
from tests.accounts.yubikey import FakeYubico, FakeYubiKey, pem
from tests.core.conftest import Authority, make_authority

SERIAL = 1000009
_keys: dict[int, FakeYubiKey] = {}


@cache
def _environment() -> tuple[FakeYubico, Authority, Path, Path]:
    """Künstliche Yubico-Wurzel und Zeitstempeldienst, einmal je Testlauf."""
    folder = Path(tempfile.mkdtemp(prefix="psagent-signieren-"))
    yubico, authority = FakeYubico(), make_authority("Signieren")
    (folder / "yubico.pem").write_bytes(pem(yubico.root))
    (folder / "tsa.pem").write_bytes(authority.ca_pem)
    return yubico, authority, folder / "yubico.pem", folder / "tsa.pem"


@contextmanager
def signing_environment() -> Iterator[FakeYubico]:
    """Einstellungen für Zertifikatsliste und Zeitstempel, nur für die Dauer des Blocks."""
    yubico, authority, yubico_path, tsa_path = _environment()
    with (
        override_settings(
            PSAGENT_YUBICO_CA_PATH=str(yubico_path),
            PSAGENT_YUBIKEY_SERIALS=[str(SERIAL)],
            PSAGENT_TSA_URL="https://tsa.example.org",
            PSAGENT_TSA_CA_PATH=str(tsa_path),
        ),
        mock.patch("core.timestamp.stamper", lambda: authority.stamper),
    ):
        yield yubico


def yubikey_of(user: User) -> FakeYubiKey:
    """Künstlicher YubiKey der Person; das Zertifikat steht in der Liste (in der Umgebung)."""
    yubico, *_ = _environment()
    key = _keys.get(user.pk)
    if (
        key is None
        or not user.signing_certificates.filter(
            sha256=certificates.fingerprint(key.certificate)
        ).exists()
    ):
        key = yubico.yubikey(SERIAL)
        certificates.register(user, **key.upload(), name="YubiKey (Testdaten)", actor=None)
        _keys[user.pk] = key
    return key


def sign_pdf(pdf: bytes, key: FakeYubiKey, *, field: str = SIGNATURE_FIELD) -> bytes:
    """Signiert wie das Signier-Tool: als Nachtrag in das vorbereitete Feld."""
    der = serialization.Encoding.DER
    cert = asn1_x509.Certificate.load(key.certificate.public_bytes(der))
    private = asn1_keys.PrivateKeyInfo.load(
        key.key.private_bytes(der, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    )
    signer = signers.SimpleSigner(
        signing_cert=cert,
        signing_key=private,
        cert_registry=SimpleCertificateStore.from_certs([cert]),  # type: ignore[no-untyped-call]
    )
    output = signers.sign_pdf(
        IncrementalPdfFileWriter(BytesIO(pdf)),
        signers.PdfSignatureMetadata(field_name=field),
        signer=signer,
    )
    signed: bytes = output.getvalue()
    return signed


def signed_protocol(round_: InspectionRound, *, key: FakeYubiKey | None = None) -> bytes:
    """Das gespeicherte Prüfprotokoll, signiert mit dem YubiKey des Prüfers (oder `key`)."""
    assert round_.inspector is not None
    with signing_environment():
        key = key or yubikey_of(round_.inspector)
    return sign_pdf(bytes(RoundProtocol.objects.get(round=round_).pdf), key)


def sign(round_: InspectionRound, *, actor: User, pdf: bytes | None = None) -> RoundEvent:
    """Reicht das signierte Protokoll ein (Standard: korrekt signiert vom Prüfer der Runde)."""
    with signing_environment():
        if pdf is None:
            ready = (
                round_.inspector_id is not None
                and RoundProtocol.objects.filter(round=round_).exists()
            )
            # Ohne Protokoll (offene Runde, Altbestand) lehnt sign_round vor der Prüfung ab.
            pdf = signed_protocol(round_) if ready else b""
        return services.sign_round(round_, actor=actor, signed_pdf=pdf)
