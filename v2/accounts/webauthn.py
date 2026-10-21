"""Passkeys (FIDO2/WebAuthn) mit py_webauthn: registrieren und anmelden (#11).

Die Kryptografie übernimmt ausschließlich py_webauthn (CLAUDE.md, Regel 6). Hier stehen nur
der Ablauf und die Regeln:

- Die Challenge liegt in der Sitzung und gilt für genau einen Versuch.
- Benutzerprüfung (PIN, Biometrie) ist immer Pflicht.
- Anmeldung ohne Benutzernamen: Der Passkey liegt auf dem Schlüssel (resident key) und nennt
  das Konto selbst.
- Widerrufene Passkeys und deaktivierte Konten werden abgewiesen. Sinkt der Signaturzähler, ist
  der Schlüssel womöglich geklont; dann wird ebenfalls abgewiesen.
- Admin und PSA-Sachkundige fragen beim Einrichten die Herstellerbescheinigung an. Stammt sie
  nachweislich von Yubico (Kette bis `PSAGENT_YUBICO_FIDO_CA_PATH`, geprüft von py_webauthn),
  gilt der Passkey als YubiKey (`Passkey.yubikey_attested`, #95). Änderungen bestätigt er erst
  nach der Freigabe: Den ersten YubiKey eines Kontos gibt der Server frei
  (`manage.py yubikey_freigeben`), jeden weiteren ein schon freigegebener YubiKey. Wer nur eine
  fremde Sitzung übernimmt, kann so keinen eigenen YubiKey unterschieben.
"""

import json
from typing import TYPE_CHECKING, Any

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from django.conf import settings
from django.contrib.sessions.backends.base import SessionBase
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from django.utils import timezone
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import (
    base64url_to_bytes,
    bytes_to_base64url,
    parse_attestation_object,
    parse_authentication_credential_json,
)
from webauthn.helpers.exceptions import InvalidAuthenticationResponse, InvalidRegistrationResponse
from webauthn.helpers.structs import (
    AttestationConveyancePreference,
    AttestationFormat,
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)
from webauthn.registration.verify_registration_response import VerifiedRegistration

from accounts.models import Passkey, User

if TYPE_CHECKING:
    from django.db.models import QuerySet

REGISTRATION_CHALLENGE = "webauthn_registration_challenge"
AUTHENTICATION_CHALLENGE = "webauthn_authentication_challenge"
CONFIRMATION_CHALLENGE = "webauthn_confirmation_challenge"


class PasskeyError(Exception):
    """Ein Passkey-Vorgang ist gescheitert. Die Meldung ist für die Oberfläche gedacht."""


def _take_challenge(session: SessionBase, key: str) -> bytes:
    raw = session.pop(key, None)
    if not isinstance(raw, str):
        raise PasskeyError("Der Vorgang ist abgelaufen. Bitte noch einmal beginnen.")
    return base64url_to_bytes(raw)


def _store_challenge(session: SessionBase, key: str, challenge: bytes) -> None:
    session[key] = bytes_to_base64url(challenge)


def registration_options(user: User, session: SessionBase) -> str:
    """Optionen für einen neuen Passkey (JSON für `navigator.credentials.create`)."""
    existing = [
        PublicKeyCredentialDescriptor(id=bytes(passkey.credential_id))
        for passkey in user.passkeys.filter(revoked_at__isnull=True)
    ]
    options = generate_registration_options(
        rp_id=settings.PSAGENT_WEBAUTHN_RP_ID,
        rp_name=settings.PSAGENT_WEBAUTHN_RP_NAME,
        user_id=user.webauthn_id.bytes,
        user_name=user.get_username(),
        user_display_name=user.get_full_name() or user.get_username(),
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.REQUIRED,
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
        exclude_credentials=existing,
        attestation=AttestationConveyancePreference.DIRECT
        if user.requires_passkey
        else AttestationConveyancePreference.NONE,
    )
    _store_challenge(session, REGISTRATION_CHALLENGE, options.challenge)
    return options_to_json(options)


@transaction.atomic
def register(
    user: User,
    credential: str | dict[str, Any],
    name: str,
    session: SessionBase,
    *,
    approved: bool = False,
) -> Passkey:
    """Prüft die Antwort des Schlüssels und speichert den Passkey.

    `approved`: Die Einrichtung wurde mit einem freigegebenen YubiKey des Kontos bestätigt; ein
    neuer YubiKey ist dann gleich freigegeben.
    """
    challenge = _take_challenge(session, REGISTRATION_CHALLENGE)
    name = name.strip()[:100] or "Passkey"
    try:
        verified = _verify_registration(credential, challenge)
    except InvalidRegistrationResponse as error:
        raise PasskeyError("Der Passkey konnte nicht geprüft werden.") from error
    if Passkey.objects.filter(credential_id=verified.credential_id).exists():
        raise PasskeyError("Dieser Passkey ist bereits eingetragen.")
    transports = _transports(credential)
    attested = _attested_by_yubico(credential, challenge, verified)
    return Passkey.objects.create(
        user=user,
        name=name,
        credential_id=verified.credential_id,
        public_key=verified.credential_public_key,
        sign_count=verified.sign_count,
        transports=transports,
        aaguid=str(verified.aaguid),
        yubikey_attested=attested,
        yubikey_approved_at=timezone.now() if attested and approved else None,
    )


def _verify_registration(
    credential: str | dict[str, Any], challenge: bytes, roots: list[bytes] | None = None
) -> VerifiedRegistration:
    return verify_registration_response(
        credential=credential,
        expected_challenge=challenge,
        expected_rp_id=settings.PSAGENT_WEBAUTHN_RP_ID,
        expected_origin=settings.PSAGENT_WEBAUTHN_ORIGINS,
        require_user_verification=True,
        pem_root_certs_bytes_by_fmt={AttestationFormat.PACKED: roots} if roots else None,
    )


def yubico_fido_roots() -> list[bytes]:
    """Wurzeln der FIDO-Bescheinigung von Yubico (PEM); leer, wenn nicht eingerichtet."""
    path = str(settings.PSAGENT_YUBICO_FIDO_CA_PATH or "").strip()
    if not path:
        return []
    try:
        with open(path, "rb") as file:
            certificates = x509.load_pem_x509_certificates(file.read())
    except (OSError, ValueError) as error:
        raise ImproperlyConfigured("PSAGENT_YUBICO_FIDO_CA_PATH ist nicht lesbar.") from error
    return [c.public_bytes(serialization.Encoding.PEM) for c in certificates]


def _attested_by_yubico(
    credential: str | dict[str, Any], challenge: bytes, verified: VerifiedRegistration
) -> bool:
    """Stammt die Herstellerbescheinigung nachweislich von Yubico?

    Nur das Format „packed“ mit Zertifikatskette (x5c); die Kette prüft py_webauthn gegen die
    Wurzel von Yubico. Ohne Kette (Selbstbescheinigung) oder ohne Einstellung: nein.
    """
    roots = yubico_fido_roots()
    if not roots or verified.fmt != AttestationFormat.PACKED:
        return False
    if not parse_attestation_object(verified.attestation_object).att_stmt.x5c:
        return False
    try:
        _verify_registration(credential, challenge, roots)
    except InvalidRegistrationResponse:
        return False
    return True


def _transports(credential: str | dict[str, Any]) -> list[str]:
    data = json.loads(credential) if isinstance(credential, str) else credential
    response = data.get("response", {}) if isinstance(data, dict) else {}
    transports = response.get("transports", []) if isinstance(response, dict) else []
    return [str(t) for t in transports if isinstance(t, str)][:10]


def authentication_options(session: SessionBase) -> str:
    """Optionen für die Anmeldung ohne Benutzernamen (JSON für `navigator.credentials.get`)."""
    options = generate_authentication_options(
        rp_id=settings.PSAGENT_WEBAUTHN_RP_ID,
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    _store_challenge(session, AUTHENTICATION_CHALLENGE, options.challenge)
    return options_to_json(options)


@transaction.atomic
def authenticate(credential: str | dict[str, Any], session: SessionBase) -> User:
    """Prüft eine Anmeldung per Passkey und liefert das Konto."""
    challenge = _take_challenge(session, AUTHENTICATION_CHALLENGE)
    try:
        parsed = parse_authentication_credential_json(credential)
    except (InvalidAuthenticationResponse, ValueError, TypeError) as error:
        raise PasskeyError("Die Anmeldung mit Passkey ist fehlgeschlagen.") from error
    passkey = (
        Passkey.objects.select_for_update()
        .select_related("user")
        .filter(credential_id=parsed.raw_id, revoked_at__isnull=True)
        .first()
    )
    if passkey is None or not passkey.user.is_active:
        raise PasskeyError("Die Anmeldung mit Passkey ist fehlgeschlagen.")
    user_handle = parsed.response.user_handle
    if user_handle is not None and user_handle != passkey.user.webauthn_id.bytes:
        raise PasskeyError("Die Anmeldung mit Passkey ist fehlgeschlagen.")
    try:
        verified = verify_authentication_response(
            credential=parsed,
            expected_challenge=challenge,
            expected_rp_id=settings.PSAGENT_WEBAUTHN_RP_ID,
            expected_origin=settings.PSAGENT_WEBAUTHN_ORIGINS,
            credential_public_key=bytes(passkey.public_key),
            credential_current_sign_count=passkey.sign_count,
            require_user_verification=True,
        )
    except InvalidAuthenticationResponse as error:
        raise PasskeyError("Die Anmeldung mit Passkey ist fehlgeschlagen.") from error
    passkey.sign_count = verified.new_sign_count
    passkey.last_used_at = timezone.now()
    passkey.save(update_fields=["sign_count", "last_used_at"])
    return passkey.user


# --- Änderung mit YubiKey bestätigen (#95) ---


def yubikeys(user: User) -> "QuerySet[Passkey]":
    """Passkeys, die Änderungen bestätigen: bescheinigt, freigegeben, nicht widerrufen."""
    return user.passkeys.filter(
        revoked_at__isnull=True, yubikey_attested=True, yubikey_approved_at__isnull=False
    )


def confirmation_options(user: User, session: SessionBase, path: str) -> str:
    """Optionen für die Bestätigung genau einer Anfrage an `path` mit einem YubiKey des Kontos."""
    keys = yubikeys(user)
    if (
        not keys.exists()
        and user.passkeys.filter(revoked_at__isnull=True, yubikey_attested=True).exists()
    ):
        raise PasskeyError(
            "Ihr YubiKey ist noch nicht freigegeben. Den ersten YubiKey eines Kontos gibt der "
            "Server frei: manage.py yubikey_freigeben <Benutzername>."
        )
    if not keys.exists():
        raise PasskeyError(
            "Für Änderungen in der Nutzerverwaltung braucht dieses Konto einen YubiKey mit "
            "Herstellerbescheinigung. Bitte unter „Konto“ einen YubiKey als Passkey einrichten."
        )
    options = generate_authentication_options(
        rp_id=settings.PSAGENT_WEBAUTHN_RP_ID,
        allow_credentials=[
            PublicKeyCredentialDescriptor(id=bytes(key.credential_id)) for key in keys
        ],
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    session[CONFIRMATION_CHALLENGE] = {
        "challenge": bytes_to_base64url(options.challenge),
        "path": path,
    }
    return options_to_json(options)


@transaction.atomic
def confirm(
    user: User, credential: str | dict[str, Any], session: SessionBase, path: str
) -> Passkey:
    """Prüft die Bestätigung: einmalig, für genau diese Adresse, mit einem YubiKey des Kontos."""
    failed = "Die Bestätigung mit dem YubiKey ist fehlgeschlagen. Bitte noch einmal versuchen."
    pending = session.pop(CONFIRMATION_CHALLENGE, None)
    if not isinstance(pending, dict) or pending.get("path") != path:
        raise PasskeyError(failed)
    try:
        parsed = parse_authentication_credential_json(credential)
    except (InvalidAuthenticationResponse, ValueError, TypeError) as error:
        raise PasskeyError(failed) from error
    passkey = yubikeys(user).select_for_update().filter(credential_id=parsed.raw_id).first()
    if passkey is None:
        raise PasskeyError(failed)
    try:
        verified = verify_authentication_response(
            credential=parsed,
            expected_challenge=base64url_to_bytes(str(pending.get("challenge", ""))),
            expected_rp_id=settings.PSAGENT_WEBAUTHN_RP_ID,
            expected_origin=settings.PSAGENT_WEBAUTHN_ORIGINS,
            credential_public_key=bytes(passkey.public_key),
            credential_current_sign_count=passkey.sign_count,
            require_user_verification=True,
        )
    except InvalidAuthenticationResponse as error:
        raise PasskeyError(failed) from error
    passkey.sign_count = verified.new_sign_count
    passkey.last_used_at = timezone.now()
    passkey.save(update_fields=["sign_count", "last_used_at"])
    return passkey


def revoke(passkey: Passkey) -> None:
    """Widerruft einen Passkey; der letzte eines Kontos mit Passkey-Pflicht bleibt bestehen."""
    active = passkey.user.passkeys.filter(revoked_at__isnull=True)
    if passkey.user.requires_passkey and active.exclude(pk=passkey.pk).count() == 0:
        raise PasskeyError(
            "Das ist der letzte Passkey dieses Kontos. Bitte zuerst einen weiteren einrichten."
        )
    passkey.revoked_at = timezone.now()
    passkey.save(update_fields=["revoked_at"])
