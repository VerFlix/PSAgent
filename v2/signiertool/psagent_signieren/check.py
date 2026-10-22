"""Signierten Prüfbericht ohne Datenbank prüfen (`signieren pruefen`, #19 Schritt 2).

Für Felix und den Vorstand, auf jedem Rechner, unabhängig vom Server (Konzept: „Das
Prüfwerkzeug auf einem anderen Rechner vergleicht mit dieser Liste, nicht mit der auf dem Pi“).
Geprüft wird das PDF, wie es die Seite „Prüfberichte“ liefert:

1. Es ist ein PSAgent-Prüfprotokoll mit genau einer Signatur im Feld „Sachkundiger“ und genau
   einem Zeitstempel.
2. Die Signatur ist intakt; nach ihr kam nur der Zeitstempel.
3. Der Fingerabdruck des Signaturzertifikats steht auf der Liste (`--fingerabdruecke`).
4. Der Zeitstempel ist gültig und stammt vom Dienst, dessen Zertifizierungsstelle angegeben ist
   (`--tsa-ca`).
5. Runde, Prüfsumme des Inhalts und Kettenkopf liest das Tool aus dem signierten Teil
   (Schlüsselwörter, wie `inspections.protocol.read_anchor` im Server) und zeigt sie an.

Ohne Datenbank nicht prüfbar sind Sachkunde, Zertifikatsliste und die Ergebnisse in PSAgent;
das macht `manage.py kette_pruefen` auf dem Server, mit dem angezeigten Kettenkopf.

Alles Kryptografische übernimmt pyHanko (CLAUDE.md, Regel 6).
"""

import hashlib
import re
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path

from asn1crypto import pem, x509
from pyhanko.pdf_utils.reader import PdfFileReader
from pyhanko.sign.diff_analysis import ModificationLevel
from pyhanko.sign.validation import validate_pdf_signature, validate_pdf_timestamp
from pyhanko.sign.validation.pdf_embedded import EmbeddedPdfSignature
from pyhanko_certvalidator import ValidationContext

from psagent_signieren.core import SIGNATURE_FIELD

UNCHANGED, CHANGED, UNCHECKABLE = "unverändert", "verändert", "nicht prüfbar"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
# SHA-256 als 64 Hexziffern, auch mit Doppelpunkten; wie `core.audit` im Server.
_FINGERPRINT = re.compile(r"\b[0-9A-Fa-f]{2}(?::?[0-9A-Fa-f]{2}){31}\b")


def printable(text: str) -> str:
    """Ersetzt Steuerzeichen aus dem PDF durch „?“, damit sie die Ausgabe nicht verfälschen."""
    return "".join(char if char.isprintable() else "?" for char in text)


class InputError(ValueError):
    """Eine angegebene Datei fehlt oder ist unbrauchbar; der Text sagt, welche."""


@dataclass(frozen=True)
class SignedValues:
    """Die mitsignierten Werte des Protokolls (Schlüsselwörter des PDFs)."""

    round_id: int
    content_sha256: str
    chain_seq: int
    chain_hash: str


@dataclass
class Result:
    findings: list[str] = field(default_factory=list)
    unchecked: list[str] = field(default_factory=list)
    confirmed: list[str] = field(default_factory=list)
    info: list[str] = field(default_factory=list)

    @property
    def verdict(self) -> str:
        if self.findings:
            return CHANGED
        return UNCHECKABLE if self.unchecked else UNCHANGED


def read_fingerprints(path: Path) -> set[str]:
    """SHA-256-Fingerabdrücke aus einer Textdatei; `#` leitet Kommentare ein."""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as error:
        raise InputError(f"{path} lässt sich nicht lesen.") from error
    found = {
        match.group(0).replace(":", "").lower()
        for line in lines
        for match in _FINGERPRINT.finditer(line.partition("#")[0])
    }
    if not found:
        raise InputError(f"In {path} steht kein SHA-256-Fingerabdruck.")
    return found


def read_tsa_roots(path: Path) -> list[x509.Certificate]:
    """Zertifizierungsstelle des Zeitstempeldienstes (PEM oder DER)."""
    try:
        data = path.read_bytes()
        blocks = pem.unarmor(data, multiple=True) if pem.detect(data) else [(None, None, data)]
        return [x509.Certificate.load(der) for _, _, der in blocks]
    except OSError as error:
        raise InputError(f"{path} lässt sich nicht lesen.") from error
    except Exception as error:
        raise InputError(f"{path} ist kein Zertifikat.") from error


def signed_values(pdf: bytes) -> SignedValues | None:
    """Liest die Schlüsselwörter aus dem Stand, den die Signatur abdeckt."""
    try:
        reader = PdfFileReader(BytesIO(pdf))
        [signature] = reader.embedded_regular_signatures
        byte_range = [int(n) for n in signature.sig_object["/ByteRange"]]
        covered = pdf[: byte_range[2] + byte_range[3]]
        text = str(PdfFileReader(BytesIO(covered)).trailer_view["/Info"].get("/Keywords", ""))
        values = dict(item.strip().partition(":")[::2] for item in text.split(","))
        seq, _, chain_hash = values["psagent-kettenkopf"].partition(":")
        result = SignedValues(
            int(values["psagent-runde"]), values["psagent-inhalt"], int(seq), chain_hash
        )
    except Exception:
        return None
    if not (_SHA256.match(result.content_sha256) and _SHA256.match(result.chain_hash)):
        return None
    return result


def check(
    pdf: bytes,
    *,
    tsa_roots: list[x509.Certificate] | None = None,
    fingerprints: set[str] | None = None,
) -> Result:
    """Prüft einen signierten Prüfbericht; braucht weder Datenbank noch Netz."""
    result = Result()
    try:
        reader = PdfFileReader(BytesIO(pdf))
        signatures = reader.embedded_regular_signatures
        stamps = reader.embedded_timestamp_signatures
    except Exception:
        result.unchecked.append("Die Datei ist kein lesbares PDF.")
        return result
    if len(signatures) != 1 or signatures[0].field_name != SIGNATURE_FIELD:
        result.unchecked.append(
            f"Kein PSAgent-Prüfbericht: genau eine Signatur im Feld „{SIGNATURE_FIELD}“ fehlt."
        )
        return result
    signature = signatures[0]
    _check_signature(signature, stamps, result)
    _check_signer(signature, fingerprints, result)
    if stamps:
        _check_stamp(stamps, tsa_roots, result)
    values = signed_values(pdf)
    if values is None:
        result.unchecked.append("Das Protokoll enthält keine mitsignierten Prüfwerte.")
    else:
        head = f"{values.chain_seq}:{values.chain_hash}"
        result.info += [
            f"Runde: {values.round_id}",
            f"Inhalt (SHA-256): {values.content_sha256}",
            f"Kettenkopf: {head}",
            "Auf dem Server prüfen (Ergebnisse, Sachkunde, Kette):",
            f"  uv run python manage.py kette_pruefen --kopf {head}",
        ]
    return result


def _check_signature(
    signature: EmbeddedPdfSignature, stamps: list[EmbeddedPdfSignature], result: Result
) -> None:
    signer = signature.signer_cert
    try:
        status = validate_pdf_signature(
            signature,
            ValidationContext(trust_roots=[signer], allow_fetching=False),
        )
    except Exception:
        result.findings.append("Die Signatur lässt sich nicht prüfen.")
        return
    if not (status.intact and status.valid):
        result.findings.append("Die Signatur passt nicht zum Inhalt.")
        return
    if len(stamps) > 1:
        result.findings.append("Das PDF hat mehr als einen Zeitstempel.")
        return
    if stamps and stamps[0].signed_revision <= signature.signed_revision:
        result.findings.append("Der Zeitstempel liegt vor der Signatur und deckt sie nicht ab.")
        return
    # Ohne Zeitstempel darf nach der Signatur nichts mehr kommen, mit ihm nur der Stempel.
    allowed = ModificationLevel.LTA_UPDATES if stamps else ModificationLevel.NONE
    level = status.modification_level
    if level is None or level > allowed:
        result.findings.append("Nach der Signatur wurde das PDF geändert.")
        return
    if not stamps:
        result.confirmed.append("Signatur intakt, danach keine Änderung")
        result.unchecked.append(
            "Kein Zeitstempel: Das PDF ist signiert, aber noch nicht in PSAgent hochgeladen."
        )
        return
    result.confirmed.append("Signatur intakt, danach nur der Zeitstempel")


def _check_signer(
    signature: EmbeddedPdfSignature, fingerprints: set[str] | None, result: Result
) -> None:
    signer = signature.signer_cert
    digest = hashlib.sha256(signer.dump()).hexdigest()
    result.info.append(f"Signaturzertifikat (SHA-256): {digest}")
    result.info.append(f"Inhaber laut Zertifikat: {printable(signer.subject.human_friendly)}")
    if fingerprints is None:
        result.unchecked.append(
            "Fingerabdruck nicht abgeglichen: Liste mit --fingerabdruecke angeben "
            "oder von Hand mit der Papierliste vergleichen."
        )
    elif digest not in fingerprints:
        result.findings.append("Das Signaturzertifikat steht nicht auf der Liste.")
    else:
        result.confirmed.append("Signaturzertifikat steht auf der Liste")


def _check_stamp(
    stamps: list[EmbeddedPdfSignature], roots: list[x509.Certificate] | None, result: Result
) -> None:
    if roots is None:
        result.unchecked.append(
            "Zeitstempel nicht geprüft: Zertifizierungsstelle des Dienstes mit --tsa-ca angeben."
        )
        return
    try:
        status = validate_pdf_timestamp(
            stamps[0],
            validation_context=ValidationContext(trust_roots=roots, allow_fetching=False),
        )
    except Exception:
        result.findings.append("Der Zeitstempel lässt sich nicht prüfen.")
        return
    if not (status.intact and status.valid and status.trusted):
        result.findings.append(
            "Der Zeitstempel ist ungültig oder stammt nicht vom angegebenen Dienst."
        )
        return
    result.confirmed.append(f"Zeitstempel gültig ({status.timestamp:%d.%m.%Y %H:%M} UTC)")
