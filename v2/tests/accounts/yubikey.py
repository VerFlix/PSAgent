"""Künstliche YubiKeys für Tests der Zertifikatsliste (#18). Kein echter Schlüssel, kein Netz.

Nachgebaut ist die Kette der Yubico-PIV-Bescheinigung: Wurzel von „Yubico“ → Gerätezertifikat
(Slot f9) → Bescheinigung für Slot 9c mit Seriennummer und PIN-/Berührungsregel. Dazu das
selbst signierte Zertifikat in Slot 9c, wie es `ykman` anlegt. Alles erzeugt `cryptography`.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from asn1crypto import core as asn1
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from accounts.certificates import OID_POLICY, OID_SERIAL, PIN_ALWAYS, TOUCH_ALWAYS

Key = ec.EllipticCurvePrivateKey


def name(common_name: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])


def new_key() -> Key:
    return ec.generate_private_key(ec.SECP256R1())


def issue(
    subject: x509.Name,
    public_key: ec.EllipticCurvePublicKey,
    issuer: x509.Name,
    signer: Key,
    *,
    ca: bool = False,
    extensions: tuple[x509.ExtensionType, ...] = (),
    valid_from: datetime | None = None,
    days: int = 365,
) -> x509.Certificate:
    start = valid_from or datetime.now(UTC) - timedelta(days=1)
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(public_key)
        .serial_number(x509.random_serial_number())
        .not_valid_before(start)
        .not_valid_after(start + timedelta(days=days))
    )
    if ca:
        builder = builder.add_extension(x509.BasicConstraints(ca=True, path_length=None), True)
    for extension in extensions:
        builder = builder.add_extension(extension, critical=False)
    return builder.sign(signer, hashes.SHA256())


def pem(*certificates: x509.Certificate) -> bytes:
    return b"".join(c.public_bytes(serialization.Encoding.PEM) for c in certificates)


def der(certificate: x509.Certificate) -> bytes:
    return certificate.public_bytes(serialization.Encoding.DER)


@dataclass
class FakeYubiKey:
    """Ein künstlicher YubiKey: Schlüssel, Zertifikat aus Slot 9c und Bescheinigung."""

    serial: int
    key: Key
    certificate: x509.Certificate
    attestation: x509.Certificate
    intermediate: x509.Certificate

    def upload(self) -> dict[str, bytes]:
        """Was der Admin hochlädt (Argumente von `certificates.register`)."""
        return {
            "certificate": der(self.certificate),
            "attestation": pem(self.attestation),
            "intermediates": pem(self.intermediate),
        }


@dataclass
class FakeYubico:
    """Künstliche Wurzel der Bescheinigungen; ersetzt in Tests das Zertifikat von Yubico."""

    key: Key = field(default_factory=new_key)
    root: x509.Certificate = field(init=False)

    def __post_init__(self) -> None:
        subject = name("Yubico PIV Root CA (Testdaten)")
        self.root = issue(subject, self.key.public_key(), subject, self.key, ca=True, days=3650)

    def yubikey(
        self,
        serial: int,
        *,
        pin: int = PIN_ALWAYS,
        touch: int = TOUCH_ALWAYS,
        slot: str = "9c",
        holder: str = "Testperson Prüfer (Testdaten)",
        key: Key | None = None,
        valid_from: datetime | None = None,
        days: int = 365,
        device_ca: bool = True,
        device_serial: int | None = None,
    ) -> FakeYubiKey:
        """`device_ca=False`: Gerätezertifikat ohne BasicConstraints (wie bei älteren Geräten)."""
        device_key, slot_key = new_key(), key or new_key()
        intermediate = issue(
            name("Yubico PIV Attestation"),
            device_key.public_key(),
            self.root.subject,
            self.key,
            ca=device_ca,
            extensions=()
            if device_serial is None
            else (x509.UnrecognizedExtension(OID_SERIAL, asn1.Integer(device_serial).dump()),),
            days=3650,
        )
        attestation = issue(
            name(f"YubiKey PIV Attestation {slot}"),
            slot_key.public_key(),
            intermediate.subject,
            device_key,
            extensions=(
                x509.UnrecognizedExtension(OID_SERIAL, asn1.Integer(serial).dump()),
                x509.UnrecognizedExtension(OID_POLICY, bytes([pin, touch])),
            ),
            days=3650,
        )
        certificate = issue(
            name(holder),
            slot_key.public_key(),
            name(holder),
            slot_key,
            valid_from=valid_from,
            days=days,
        )
        return FakeYubiKey(serial, slot_key, certificate, attestation, intermediate)
