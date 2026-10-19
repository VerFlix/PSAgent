"""Ein eigener Zeitstempeldienst für Tests (RSA; pyHankos Testdienst kann nur RSA).

Zertifizierungsstelle und TSA-Zertifikat entstehen bei jedem Lauf neu; nichts davon liegt im Repo,
und es geht nichts ins Netz.
"""

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from asn1crypto import keys as asn1_keys
from asn1crypto import x509 as asn1_x509
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from django.test import override_settings
from pyhanko.sign.timestamps import DummyTimeStamper
from pyhanko_certvalidator.registry import SimpleCertificateStore


@dataclass
class Authority:
    """Zertifizierungsstelle mit einem Zertifikat für Zeitstempel."""

    ca_pem: bytes
    stamper: DummyTimeStamper


def _name(text: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, text)])


def make_authority(label: str = "Test") -> Authority:
    now = datetime.now(UTC)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca = (
        x509.CertificateBuilder()
        .subject_name(_name(f"{label}-CA (künstlich)"))
        .issuer_name(_name(f"{label}-CA (künstlich)"))
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=30))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(True, False, False, False, False, True, True, False, False),
            critical=True,
        )
        .sign(ca_key, hashes.SHA256())
    )
    tsa_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    tsa = (
        x509.CertificateBuilder()
        .subject_name(_name(f"{label}-TSA (künstlich)"))
        .issuer_name(ca.subject)
        .public_key(tsa_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=30))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(True, False, False, False, False, False, False, False, False),
            critical=True,
        )
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.TIME_STAMPING]), critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    der = serialization.Encoding.DER
    key_der = tsa_key.private_bytes(
        der, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    tsa_cert = asn1_x509.Certificate.load(tsa.public_bytes(der))
    stamper = DummyTimeStamper(
        tsa_cert=tsa_cert,
        tsa_key=asn1_keys.PrivateKeyInfo.load(key_der),
        certs_to_embed=SimpleCertificateStore.from_certs([tsa_cert]),  # type: ignore[no-untyped-call]
    )
    return Authority(ca.public_bytes(serialization.Encoding.PEM), stamper)


@pytest.fixture
def tsa(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Authority]:
    authority = make_authority()
    ca_path = tmp_path / "tsa-ca.pem"
    ca_path.write_bytes(authority.ca_pem)
    monkeypatch.setattr("core.timestamp.stamper", lambda: authority.stamper)
    with override_settings(
        PSAGENT_TSA_URL="https://tsa.example.org", PSAGENT_TSA_CA_PATH=str(ca_path)
    ):
        yield authority
