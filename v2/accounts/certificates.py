"""Zertifikatsliste: wessen Signatur der Server annimmt (#18, Konzept „Zertifikatsliste schützen“).

Die Liste enthält nur öffentliche Schlüssel. Gefährlich wäre, wenn sie sich unbemerkt ändern
ließe; dann könnte jemand eine fremde Signatur unterschieben. Darum:

1. **Herstellerbescheinigung.** Aufgenommen wird ein Zertifikat nur mit der Attestation von
   Yubico für Slot 9c: Sie belegt, dass der Schlüssel im YubiKey erzeugt wurde und ihn nie
   verlässt. Angenommen werden nur die Seriennummern aus `PSAGENT_YUBIKEY_SERIALS` und nur mit
   „PIN und Berührung bei jeder Signatur“. Das Wurzelzertifikat von Yubico liegt außerhalb der
   Datenbank (`PSAGENT_YUBICO_CA_PATH`).
2. **Hash-Kette.** Aufnahme und Widerruf sind eigene Vorgänge, die nur angehängt werden
   (`SigningCertificate`, `SigningCertificateRevocation`). Sie stecken im täglichen
   Kettenkopf an den Vorstand, die Mail nennt zusätzlich die Fingerabdrücke.
3. **Prüfen zum Signaturzeitpunkt.** `signer_at` nimmt eine Signatur nur an, wenn das
   Zertifikat zu diesem Zeitpunkt hinterlegt und nicht widerrufen war und die Person gültige
   Sachkunde hatte. Vorher prüft sie die Liste gegen ihre Kettenglieder und die
   Herstellerbescheinigung erneut: Eine Zeile, die jemand an der App vorbei einfügt oder ändert,
   wird so abgelehnt.
4. **Schreibschutz.** Aufnehmen und widerrufen darf nur, wer Nutzer verwalten darf (Admin).

Alles Kryptografische übernimmt `cryptography` (CLAUDE.md, Regel 6): Signaturen der Kette
prüft `Certificate.verify_directly_issued_by`, Fingerabdrücke sind SHA-256. Die Ablaufdaten der
Bescheinigungskette prüft der Server nicht; sie beschreibt einmalig, wo der Schlüssel entstand.
"""

import hashlib
from datetime import datetime

from asn1crypto import core as asn1
from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.x509.oid import NameOID
from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from accounts.models import Role, SigningCertificate, SigningCertificateRevocation, User
from accounts.rights import Right, require
from accounts.services import AccountError
from core import chain

# Erweiterungen der Yubico-PIV-Bescheinigung (developers.yubico.com, „PIV Attestation“).
_YUBICO = "1.3.6.1.4.1.41482.3"
OID_SERIAL = x509.ObjectIdentifier(f"{_YUBICO}.7")
OID_POLICY = x509.ObjectIdentifier(f"{_YUBICO}.8")
SLOT = "9c"  # „Digital Signature“
ATTESTATION_NAME = "YubiKey PIV Attestation"
PIN_ALWAYS = 3
TOUCH_ALWAYS = 2
# Höchstens so viele Zwischenzertifikate zwischen Bescheinigung und Wurzel.
MAX_CHAIN = 4


class CertificateError(AccountError):
    """Ein Zertifikat wird nicht aufgenommen oder eine Signatur nicht angenommen; mit Grund."""


# --- Lesen und Fingerabdrücke ---


def load_certificates(data: bytes) -> list[x509.Certificate]:
    """Ein oder mehrere Zertifikate als PEM, oder genau eines als DER."""
    try:
        if b"-----BEGIN" in data:
            return x509.load_pem_x509_certificates(data)
        return [x509.load_der_x509_certificate(data)] if data else []
    except ValueError as error:
        raise CertificateError("Die Datei ist kein lesbares Zertifikat (PEM oder DER).") from error


def _single(data: bytes, what: str) -> x509.Certificate:
    certificates = load_certificates(data)
    if len(certificates) != 1:
        raise CertificateError(f"{what}: bitte genau ein Zertifikat hochladen.")
    return certificates[0]


def fingerprint(certificate: x509.Certificate) -> str:
    """SHA-256 über das ganze Zertifikat (DER), wie ihn auch PDF-Reader anzeigen."""
    return certificate.fingerprint(hashes.SHA256()).hex()


def key_fingerprint(certificate: x509.Certificate) -> str:
    """SHA-256 über den öffentlichen Schlüssel (SubjectPublicKeyInfo, DER)."""
    spki = certificate.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return hashlib.sha256(spki).hexdigest()


def _pem(certificates: list[x509.Certificate]) -> str:
    return "".join(c.public_bytes(serialization.Encoding.PEM).decode() for c in certificates)


# --- Herstellerbescheinigung ---


def _roots() -> list[x509.Certificate]:
    path = str(settings.PSAGENT_YUBICO_CA_PATH or "").strip()
    if not path:
        raise CertificateError("Nicht eingerichtet: PSAGENT_YUBICO_CA_PATH fehlt.")
    try:
        with open(path, "rb") as file:
            roots = load_certificates(file.read())
    except OSError as error:
        raise CertificateError("Wurzelzertifikat von Yubico nicht lesbar.") from error
    if not roots:
        raise CertificateError("Wurzelzertifikat von Yubico nicht lesbar.")
    return roots


def _serials() -> set[int]:
    try:
        serials = {int(item) for item in settings.PSAGENT_YUBIKEY_SERIALS}
    except ValueError as error:
        raise CertificateError("PSAGENT_YUBIKEY_SERIALS: nur Zahlen, durch Komma.") from error
    if not serials:
        raise CertificateError("Nicht eingerichtet: PSAGENT_YUBIKEY_SERIALS fehlt.")
    return serials


def _issued_by(certificate: x509.Certificate, issuer: x509.Certificate) -> bool:
    try:
        certificate.verify_directly_issued_by(issuer)
    except (ValueError, TypeError, InvalidSignature):
        return False
    return True


def _is_ca(certificate: x509.Certificate) -> bool:
    try:
        constraints = certificate.extensions.get_extension_for_class(x509.BasicConstraints)
    except x509.ExtensionNotFound:
        return False
    return constraints.value.ca


def _may_issue(certificate: x509.Certificate, attestation: x509.Certificate) -> bool:
    """Darf `certificate` in der Kette über der Bescheinigung stehen? (Erlaubnisliste)

    - Ausdrücklich als Zertifizierungsstelle gekennzeichnet (`BasicConstraints ca=True`), oder
    - das Gerätezertifikat (Slot f9), das die Bescheinigung direkt ausgestellt hat. Ältere
      tragen keine `BasicConstraints`; trägt es eine Seriennummer, muss sie passen.

    Nie eine Slot-Bescheinigung: Sonst könnte jemand mit dem Schlüssel aus Slot 9a seines
    eigenen YubiKeys eine „Bescheinigung 9c“ mit fremder Seriennummer für einen Schlüssel
    außerhalb des YubiKeys signieren.
    """
    names = certificate.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
    if any(str(name.value).startswith(ATTESTATION_NAME) for name in names):
        return False
    if _is_ca(certificate):
        return True
    try:
        certificate.extensions.get_extension_for_class(x509.BasicConstraints)
    except x509.ExtensionNotFound:
        pass
    else:
        return False  # ausdrücklich keine Zertifizierungsstelle
    if not _issued_by(attestation, certificate):
        return False
    try:
        device_serial = _extension(certificate, OID_SERIAL)
    except CertificateError:
        return True
    return device_serial == _extension(attestation, OID_SERIAL)


def _check_path(attestation: x509.Certificate, intermediates: list[x509.Certificate]) -> None:
    roots, current = _roots(), attestation
    issuers = [c for c in intermediates if _may_issue(c, attestation)]
    for _ in range(MAX_CHAIN + 1):
        if any(_issued_by(current, root) for root in roots):
            return
        issuer = next((c for c in issuers if _issued_by(current, c)), None)
        if issuer is None:
            break
        current = issuer
    raise CertificateError(
        "Die Herstellerbescheinigung lässt sich nicht bis zum Wurzelzertifikat von Yubico prüfen."
    )


def _extension(attestation: x509.Certificate, oid: x509.ObjectIdentifier) -> bytes:
    try:
        value = attestation.extensions.get_extension_for_oid(oid).value
    except x509.ExtensionNotFound as error:
        raise CertificateError("Die Herstellerbescheinigung ist unvollständig.") from error
    if not isinstance(value, x509.UnrecognizedExtension):
        raise CertificateError("Die Herstellerbescheinigung ist unvollständig.")
    return value.value


def check_attestation(
    certificate: x509.Certificate,
    attestation: x509.Certificate,
    intermediates: list[x509.Certificate],
    *,
    check_serial: bool = True,
) -> int:
    """Prüft die Bescheinigung zum Zertifikat; gibt die Seriennummer des YubiKeys zurück.

    `check_serial=False` (Rückblick, #39): Die heutige Liste zugelassener YubiKeys zählt nicht;
    ein später gestrichener YubiKey macht frühere Signaturen nicht ungültig.
    """
    names = attestation.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
    if [name.value for name in names] != [f"{ATTESTATION_NAME} {SLOT}"]:
        raise CertificateError(f"Die Herstellerbescheinigung gilt nicht für Slot {SLOT}.")
    if key_fingerprint(attestation) != key_fingerprint(certificate):
        raise CertificateError("Die Herstellerbescheinigung gehört zu einem anderen Schlüssel.")
    _check_path(attestation, intermediates)
    try:
        serial = asn1.Integer.load(_extension(attestation, OID_SERIAL)).native
    except (ValueError, TypeError) as error:
        raise CertificateError("Die Herstellerbescheinigung ist unvollständig.") from error
    if not isinstance(serial, int) or (check_serial and serial not in _serials()):
        raise CertificateError(f"Der YubiKey {serial} ist nicht zugelassen.")
    policy = _extension(attestation, OID_POLICY)
    if len(policy) < 2 or (policy[0], policy[1]) != (PIN_ALWAYS, TOUCH_ALWAYS):
        raise CertificateError("Der Schlüssel verlangt nicht PIN und Berührung bei jeder Signatur.")
    return serial


def _attestation_of(entry: SigningCertificate) -> tuple[x509.Certificate, list[x509.Certificate]]:
    stored = load_certificates(entry.attestation.encode())
    if not stored:
        raise CertificateError("Die Herstellerbescheinigung fehlt.")
    return stored[0], stored[1:]


# --- Aufnehmen und widerrufen ---


def register(
    user: User,
    *,
    certificate: bytes,
    attestation: bytes,
    intermediates: bytes,
    name: str,
    actor: User | None,
) -> SigningCertificate:
    """Nimmt ein Signaturzertifikat mit Herstellerbescheinigung in die Liste auf.

    `certificate`: Zertifikat aus Slot 9c. `attestation`: `ykman piv keys attest 9c`.
    `intermediates`: Zwischenzertifikat(e) bis zu Yubico, z. B. `ykman piv certificates export f9`.
    """
    require(actor, Right.MANAGE_USERS)
    name = name.strip()
    if not name:
        raise CertificateError("Bitte eine Bezeichnung angeben (z. B. YubiKey 1).")
    if not user.has_role(Role.PPE_EXPERT):
        raise CertificateError("Signaturzertifikate gibt es nur für PSA-Sachkundige und Admins.")
    cert = _single(certificate, "Zertifikat")
    attested = _single(attestation, "Herstellerbescheinigung")
    chain_certs = load_certificates(intermediates)
    now = timezone.now()
    if not cert.not_valid_before_utc <= now < cert.not_valid_after_utc:
        raise CertificateError("Das Zertifikat ist abgelaufen oder noch nicht gültig.")
    try:
        usage = cert.extensions.get_extension_for_class(x509.KeyUsage).value
    except x509.ExtensionNotFound:
        usage = None
    if usage is not None and not (usage.digital_signature or usage.content_commitment):
        raise CertificateError("Das Zertifikat ist nicht zum Signieren bestimmt.")
    serial = check_attestation(cert, attested, chain_certs)
    if SigningCertificate.objects.filter(sha256=fingerprint(cert)).exists():
        raise CertificateError("Dieses Zertifikat steht schon in der Liste.")
    if SigningCertificate.objects.filter(key_sha256=key_fingerprint(cert)).exists():
        raise CertificateError("Dieser Schlüssel steht schon in der Liste (auch wenn widerrufen).")
    try:
        with transaction.atomic():
            return SigningCertificate.objects.create(
                user=user,
                name=name,
                der=cert.public_bytes(serialization.Encoding.DER),
                sha256=fingerprint(cert),
                key_sha256=key_fingerprint(cert),
                subject=cert.subject.rfc4514_string()[:500],
                not_valid_before=cert.not_valid_before_utc,
                not_valid_after=cert.not_valid_after_utc,
                yubikey_serial=serial,
                attestation=_pem([attested, *chain_certs]),
                created_at=now,
                created_by=actor,
            )
    except IntegrityError as error:
        raise CertificateError("Dieses Zertifikat steht schon in der Liste.") from error


@transaction.atomic
def revoke(
    entry: SigningCertificate,
    *,
    actor: User | None,
    reason: str,
    revoked_at: datetime | None = None,
) -> SigningCertificateRevocation:
    """Widerruft (sperrt) ein Zertifikat ab `revoked_at` (Standard: jetzt, nie in der Zukunft)."""
    require(actor, Right.MANAGE_USERS)
    reason = reason.strip()
    if not reason:
        raise CertificateError("Bitte eine Begründung angeben.")
    now = timezone.now()
    moment = revoked_at or now
    if moment > now:
        raise CertificateError("Ein Widerruf gilt ab jetzt oder ab einem früheren Zeitpunkt.")
    SigningCertificate.objects.select_for_update().get(pk=entry.pk)
    if SigningCertificateRevocation.objects.filter(certificate=entry).exists():
        raise CertificateError("Dieses Zertifikat ist bereits widerrufen.")
    return SigningCertificateRevocation.objects.create(
        certificate=entry, revoked_at=moment, reason=reason, created_at=now, created_by=actor
    )


# --- Signaturen prüfen ---


def list_problems() -> list[str]:
    """Abweichungen der Zertifikatsliste von der Hash-Kette; leer = unverändert.

    Prüft die Zeilen der Liste gegen ihre Glieder und alle Glieder gegen ihre Vorgänger und
    die festgehaltenen Kettenköpfe. Wer mit Datenbankzugang die Kette ab einer Zeile der Liste
    vollständig neu berechnet und auch die festgehaltenen Köpfe entfernt, fällt erst mit den
    Köpfen aus der Zeugen-Mail auf (`kette_pruefen --kopf`).
    """
    from core import witness

    return (
        chain.verify_links(witness.known_heads())
        + chain.verify_table(SigningCertificate)
        + chain.verify_table(SigningCertificateRevocation)
    )


def signer_at(
    certificate: bytes | x509.Certificate, at: datetime, *, looking_back: bool = False
) -> SigningCertificate:
    """Eintrag, mit dessen Zertifikat zum Zeitpunkt `at` signiert werden durfte; sonst Fehler.

    Für die Prüfung hochgeladener Prüfprotokolle (#38) und den Prüfknopf an Prüfberichten (#39).
    `at` ist der Signaturzeitpunkt, belegt durch den Zeitstempel. `looking_back=True` prüft
    einen früheren Bericht: Dann zählen weder die heutige Rolle noch die heutige Liste
    zugelassener YubiKeys, nur der Stand der Zertifikatsliste und der Sachkunde zu `at`.
    """
    cert = (
        certificate
        if isinstance(certificate, x509.Certificate)
        else _single(certificate, "Zertifikat")
    )
    if list_problems():
        raise CertificateError(
            "Die Zertifikatsliste wurde an der App vorbei geändert. Bis das geklärt ist, "
            "nimmt der Server keine Signatur an (manage.py kette_pruefen)."
        )
    entry = (
        SigningCertificate.objects.select_related("user").filter(sha256=fingerprint(cert)).first()
    )
    if entry is None:
        raise CertificateError("Das Zertifikat steht nicht in der Liste der Signaturzertifikate.")
    if at < entry.created_at:
        raise CertificateError("Das Zertifikat war zum Signaturzeitpunkt noch nicht hinterlegt.")
    if SigningCertificateRevocation.objects.filter(certificate=entry, revoked_at__lte=at).exists():
        raise CertificateError("Das Zertifikat war zum Signaturzeitpunkt widerrufen.")
    if not cert.not_valid_before_utc <= at <= cert.not_valid_after_utc:
        raise CertificateError("Das Zertifikat war zum Signaturzeitpunkt nicht gültig.")
    if not looking_back and not entry.user.has_role(Role.PPE_EXPERT):
        raise CertificateError(f"{entry.user} ist nicht (mehr) PSA-Sachkundig oder deaktiviert.")
    if not entry.user.has_valid_qualification(at):
        raise CertificateError(f"{entry.user} hatte zum Signaturzeitpunkt keine gültige Sachkunde.")
    attested, chain_certs = _attestation_of(entry)
    serial = check_attestation(cert, attested, chain_certs, check_serial=not looking_back)
    if serial != entry.yubikey_serial:
        raise CertificateError("Die Herstellerbescheinigung passt nicht zum Eintrag.")
    return entry
