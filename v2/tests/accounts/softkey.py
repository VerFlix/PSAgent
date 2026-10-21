"""Simulierter FIDO2-Schlüssel für Tests (Bescheinigung „none“ oder „packed“, ES256).

Nur für Tests: erzeugt die Antworten, die ein echter Schlüssel an den Browser gibt, damit die
Prüfung im Server (py_webauthn) ohne Hardware getestet werden kann. Signiert wird mit der
Bibliothek `cryptography`; die App selbst prüft nur über py_webauthn.
"""

import base64
import hashlib
import json
import os
import struct
from dataclasses import dataclass, field
from typing import Any

import cbor2
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

UP, UV, AT = 0x01, 0x04, 0x40


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def from_b64url(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


@dataclass
class SoftKey:
    """Ein Schlüssel mit einem Passkey für eine Relying Party."""

    rp_id: str = "localhost"
    origin: str = "http://localhost:8000"
    user_verified: bool = True
    sign_count: int = 0
    credential_id: bytes = field(default_factory=lambda: os.urandom(32))
    private_key: ec.EllipticCurvePrivateKey = field(
        default_factory=lambda: ec.generate_private_key(ec.SECP256R1())
    )
    user_handle: bytes = b""
    # Herstellerbescheinigung „packed“: Schlüssel und Kette (DER), z. B. von `FakeFidoRoot`.
    attestation_key: ec.EllipticCurvePrivateKey | None = None
    attestation_chain: list[bytes] = field(default_factory=list)

    def _flags(self, extra: int = 0) -> int:
        return UP | (UV if self.user_verified else 0) | extra

    def _client_data(self, kind: str, challenge: str, origin: str | None = None) -> bytes:
        return json.dumps(
            {
                "type": kind,
                "challenge": challenge,
                "origin": origin or self.origin,
                "crossOrigin": False,
            }
        ).encode()

    def cose_key(self) -> bytes:
        numbers = self.private_key.public_key().public_numbers()
        return cbor2.dumps(
            {
                1: 2,
                3: -7,
                -1: 1,
                -2: numbers.x.to_bytes(32, "big"),
                -3: numbers.y.to_bytes(32, "big"),
            }
        )

    def register(self, options: dict[str, Any], *, origin: str | None = None) -> dict[str, Any]:
        """Antwort auf `navigator.credentials.create` zu den Optionen des Servers."""
        self.user_handle = from_b64url(options["user"]["id"])
        rp_hash = hashlib.sha256(options["rp"]["id"].encode()).digest()
        attested = bytes(16) + struct.pack(">H", len(self.credential_id)) + self.credential_id
        auth_data = (
            rp_hash
            + bytes([self._flags(AT)])
            + struct.pack(">I", self.sign_count)
            + attested
            + self.cose_key()
        )
        client_data = self._client_data("webauthn.create", options["challenge"], origin)
        statement: dict[str, Any] = {}
        if self.attestation_key is not None:
            signed = auth_data + hashlib.sha256(client_data).digest()
            statement = {
                "alg": -7,
                "sig": self.attestation_key.sign(signed, ec.ECDSA(hashes.SHA256())),
                "x5c": self.attestation_chain,
            }
        attestation = cbor2.dumps(
            {
                "fmt": "packed" if statement else "none",
                "attStmt": statement,
                "authData": auth_data,
            }
        )
        return {
            "id": b64url(self.credential_id),
            "rawId": b64url(self.credential_id),
            "type": "public-key",
            "response": {
                "clientDataJSON": b64url(client_data),
                "attestationObject": b64url(attestation),
                "transports": ["usb"],
            },
            "clientExtensionResults": {},
        }

    def sign_in(
        self,
        options: dict[str, Any],
        *,
        origin: str | None = None,
        sign_count: int | None = None,
        user_handle: bytes | None = None,
    ) -> dict[str, Any]:
        """Antwort auf `navigator.credentials.get` (Anmeldung ohne Benutzernamen)."""
        self.sign_count = self.sign_count + 1 if sign_count is None else sign_count
        rp_hash = hashlib.sha256(options["rpId"].encode()).digest()
        auth_data = rp_hash + bytes([self._flags()]) + struct.pack(">I", self.sign_count)
        client_data = self._client_data("webauthn.get", options["challenge"], origin)
        signature = self.private_key.sign(
            auth_data + hashlib.sha256(client_data).digest(), ec.ECDSA(hashes.SHA256())
        )
        handle = self.user_handle if user_handle is None else user_handle
        return {
            "id": b64url(self.credential_id),
            "rawId": b64url(self.credential_id),
            "type": "public-key",
            "response": {
                "clientDataJSON": b64url(client_data),
                "authenticatorData": b64url(auth_data),
                "signature": b64url(signature),
                "userHandle": b64url(handle) if handle else None,
            },
            "clientExtensionResults": {},
        }


@dataclass
class FakeFidoRoot:
    """Künstliche Wurzel der FIDO-Bescheinigung; ersetzt in Tests die von Yubico."""

    key: ec.EllipticCurvePrivateKey = field(
        default_factory=lambda: ec.generate_private_key(ec.SECP256R1())
    )
    root: x509.Certificate = field(init=False)

    def __post_init__(self) -> None:
        from tests.accounts.yubikey import issue, name

        subject = name("Yubico FIDO Root CA (Testdaten)")
        self.root = issue(subject, self.key.public_key(), subject, self.key, ca=True, days=3650)

    def yubikey(self) -> SoftKey:
        """Schlüssel mit Bescheinigung dieser Wurzel, wie ein YubiKey."""
        from tests.accounts.yubikey import issue

        key = ec.generate_private_key(ec.SECP256R1())
        subject = x509.Name(
            [
                x509.NameAttribute(NameOID.COUNTRY_NAME, "SE"),
                x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Yubico AB (Testdaten)"),
                x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, "Authenticator Attestation"),
                x509.NameAttribute(NameOID.COMMON_NAME, "Yubico U2F EE (Testdaten)"),
            ]
        )
        leaf = issue(
            subject,
            key.public_key(),
            self.root.subject,
            self.key,
            extensions=(x509.BasicConstraints(ca=False, path_length=None),),
        )
        return SoftKey(
            attestation_key=key,
            attestation_chain=[leaf.public_bytes(serialization.Encoding.DER)],
        )

    def pem(self) -> bytes:
        return self.root.public_bytes(serialization.Encoding.PEM)
