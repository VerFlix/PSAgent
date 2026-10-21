"""Signiertes Prüfprotokoll prüfen und mit Zeitstempel versehen (Parität 8, #38 Schritt 2).

Festlegungen (Felix, 21.10.2026): Hochladen darf nur der Prüfer der Runde. Zertifikat und
Sachkunde werden gegen den Zeitpunkt des Hochladens geprüft; die Uhrzeit in der Signatur stammt
vom PC des Prüfers und zählt nicht. Ohne Zeitstempel wird nichts gespeichert. Abgelehnte
Versuche stehen als `SignatureRejection` bei der Runde.

Geprüft wird in dieser Reihenfolge, das Billige zuerst:

1. Die Datei beginnt Byte für Byte mit dem gespeicherten Protokoll und hat genau einen
   Nachtrag mehr: die Signatur im Feld „Sachkundiger“, sonst nichts danach.
2. Der Nachtrag füllt nur das Formular aus. Wer mit der Signatur zugleich Seiten ändert, wird
   abgelehnt, auch wenn die Signatur selbst gültig wäre (Differenzprüfung von pyHanko).
3. Das Zertifikat stand beim Hochladen in der Zertifikatsliste, nicht widerrufen, und gehört
   dem Prüfer der Runde; Rolle und Sachkunde gültig (`accounts.certificates.signer_at`).
4. Die Signatur ist kryptografisch intakt und deckt die ganze Datei ab.

Danach hängt der Server einen Dokument-Zeitstempel nach RFC 3161 an (PAdES, pyHanko) und prüft
ihn gegen die Zertifizierungsstelle aus `PSAGENT_TSA_CA_PATH`.

Alles Kryptografische übernehmen pyHanko und `cryptography` (CLAUDE.md, Regel 6).
"""

import hashlib
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO

from django.core.exceptions import ImproperlyConfigured
from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
from pyhanko.pdf_utils.reader import PdfFileReader
from pyhanko.sign.diff_analysis import DEFAULT_DIFF_POLICY, ModificationLevel
from pyhanko.sign.fields import MDPPerm
from pyhanko.sign.signers.pdf_signer import PdfTimeStamper
from pyhanko.sign.validation import validate_pdf_signature, validate_pdf_timestamp
from pyhanko.sign.validation.status import SignatureCoverageLevel
from pyhanko_certvalidator import ValidationContext

from accounts import certificates
from accounts.models import SigningCertificate
from core import timestamp
from inspections.models import RoundProtocol
from inspections.protocol import SIGNATURE_FIELD

# Größer wird ein signiertes Protokoll nicht; alles darüber ist kein Prüfprotokoll.
MAX_BYTES = 20 * 1024 * 1024


class SignatureError(ValueError):
    """Das hochgeladene PDF wird nicht angenommen; der Text nennt den Grund für die Oberfläche."""


@dataclass(frozen=True)
class Checked:
    """Ergebnis einer erfolgreichen Prüfung."""

    certificate: SigningCertificate
    stamped_pdf: bytes
    stamped_at: datetime


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _revisions(data: bytes) -> int:
    return int(PdfFileReader(BytesIO(data)).xrefs.total_revisions)


def check(protocol: RoundProtocol, data: bytes, *, at: datetime) -> SigningCertificate:
    """Prüft das hochgeladene PDF gegen das gespeicherte Protokoll; gibt das Zertifikat zurück."""
    original = bytes(protocol.pdf)
    if len(data) > MAX_BYTES:
        raise SignatureError("Die Datei ist zu groß für ein Prüfprotokoll.")
    if not data.startswith(original) or len(data) == len(original):
        raise SignatureError(
            "Das ist nicht das signierte Prüfprotokoll dieser Runde. Bitte genau die "
            "heruntergeladene Datei signieren und unverändert hochladen."
        )
    try:
        reader = PdfFileReader(BytesIO(data))
        signatures = reader.embedded_signatures
        base = _revisions(original)
        total = reader.xrefs.total_revisions
    except Exception as error:
        raise SignatureError("Die Datei ist kein lesbares PDF.") from error
    if len(signatures) != 1 or signatures[0].field_name != SIGNATURE_FIELD:
        raise SignatureError(f"Erwartet wird genau eine Signatur im Feld „{SIGNATURE_FIELD}“.")
    signature = signatures[0]
    if total != base + 1 or signature.signed_revision != base:
        raise SignatureError("Nach dem Signieren wurde das PDF noch verändert.")
    try:
        diff = DEFAULT_DIFF_POLICY.apply(
            reader.get_historical_resolver(base - 1),
            reader.get_historical_resolver(base),
            None,
            MDPPerm.FILL_FORMS,
        )
    except Exception as error:
        raise SignatureError(
            "Mit der Signatur wurde mehr geändert als das Signaturfeld."
        ) from error
    if diff.modification_level > ModificationLevel.FORM_FILLING:
        raise SignatureError("Mit der Signatur wurde mehr geändert als das Signaturfeld.")
    signer = signature.signer_cert
    try:
        entry = certificates.signer_at(signer.dump(), at)
    except certificates.CertificateError as error:
        raise SignatureError(str(error)) from error
    if entry.user_id != protocol.round.inspector_id:
        raise SignatureError("Das Zertifikat gehört nicht zum Prüfer dieser Runde.")
    try:
        status = validate_pdf_signature(
            signature, ValidationContext(trust_roots=[signer], allow_fetching=False)
        )
    except Exception as error:
        raise SignatureError("Die Signatur lässt sich nicht prüfen.") from error
    if not (status.intact and status.valid and status.trusted):
        raise SignatureError("Die Signatur ist ungültig oder passt nicht zum Inhalt.")
    if status.coverage != SignatureCoverageLevel.ENTIRE_FILE:
        raise SignatureError("Die Signatur deckt nicht die ganze Datei ab.")
    return entry


def stamp(data: bytes) -> tuple[bytes, datetime]:
    """Hängt einen Dokument-Zeitstempel an und prüft ihn; gibt PDF und Zeit des Stempels zurück."""
    try:
        roots = timestamp.trust_roots()
        writer = IncrementalPdfFileWriter(BytesIO(data))
        output = PdfTimeStamper(timestamp.stamper()).timestamp_pdf(writer, "sha256")
        stamped = output.getvalue()
        reader = PdfFileReader(BytesIO(stamped))
        [document_stamp] = reader.embedded_timestamp_signatures
        status = validate_pdf_timestamp(
            document_stamp,
            validation_context=ValidationContext(trust_roots=roots, allow_fetching=False),
        )
    except ImproperlyConfigured as error:
        raise SignatureError(f"Zeitstempel nicht eingerichtet: {error}") from error
    except Exception as error:
        raise SignatureError(
            "Der Zeitstempeldienst ist nicht erreichbar. Bitte später erneut hochladen."
        ) from error
    if not (status.intact and status.valid and status.trusted):
        raise SignatureError("Der Zeitstempel ist ungültig. Bitte später erneut hochladen.")
    return stamped, status.timestamp


def verify_and_stamp(protocol: RoundProtocol, data: bytes, *, at: datetime) -> Checked:
    entry = check(protocol, data, at=at)
    stamped, stamped_at = stamp(data)
    return Checked(certificate=entry, stamped_pdf=stamped, stamped_at=stamped_at)
