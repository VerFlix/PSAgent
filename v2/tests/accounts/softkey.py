"""Simulierter FIDO2-Schlüssel für Tests (Attestierung „none“, ES256).

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
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

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

    def _cose_key(self) -> bytes:
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
            + self._cose_key()
        )
        attestation = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth_data})
        client_data = self._client_data("webauthn.create", options["challenge"], origin)
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
