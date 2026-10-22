"""Prüfwerkzeug (#19): alles auf einmal prüfen, mit verständlichem Ergebnis.

`manage.py kette_pruefen` ruft `run()` auf. Geprüft werden:

1. die ganze Hash-Kette, gegen alle bekannten Köpfe: festgehaltene (`WitnessHead`), die aus den
   signierten Prüfprotokollen, die mit `--kopf` angegebenen und die aus den Zeugen-Dateien,
2. die Zeitstempel der festgehaltenen Köpfe und ob sie aktuell sind,
   dazu jedes Bauteil gegen seinen letzten festgehaltenen Stand (`ComponentRecord`, #20),
3. Zeugen-Dateien (`--zeugen`): gespeicherte Zeugen-Mails (`.eml`) oder die Kopf-Dateien von
   Unraid (`.txt`). Jeder Kopf darin muss in der Kette stehen, sein Zeitstempel muss zum Hash
   passen und die angegebene Zeit tragen,
4. jeder signierte Prüfbericht wie mit dem Prüfknopf (`inspections.reports.check`),
5. mit `--fingerabdruecke` die Zertifikatsliste gegen eine Liste außerhalb des Servers
   (Konzept: „Das Prüfwerkzeug … vergleicht mit dieser Liste, nicht mit der auf dem Pi“).

Ergebnis: unverändert, verändert (was und wo) oder nicht prüfbar (warum, z. B. fehlende
Einstellung). Die Meldungen nennen Tabellen, Nummern und Fingerabdrücke, keine Inhalte.
Alles Kryptografische übernehmen pyHanko und `hashlib` (CLAUDE.md, Regel 6).
"""

import base64
import binascii
import email
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from email import policy
from email.message import EmailMessage
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

from core import chain, timestamp, witness
from inspections import reports
from inspections.reports import CheckResult

ZEUGEN_SUFFIXES = (".eml", ".txt")
_SEQ = re.compile(r"Kettenkopf: Nummer (\d+)")
_HASH = re.compile(r"Hash \(SHA-256\): ([0-9a-f]{64})")
_TIME = re.compile(r"Zeitstempel \(RFC 3161\): (\d{2}\.\d{2}\.\d{4} \d{2}:\d{2}:\d{2}) UTC")
_BASE64 = re.compile(r"^[A-Za-z0-9+/=]+$")
# SHA-256 als 64 Hexziffern, auch mit Doppelpunkten wie bei `openssl x509 -fingerprint`.
_FINGERPRINT = re.compile(r"\b[0-9A-Fa-f]{2}(?::?[0-9A-Fa-f]{2}){31}\b")


class AuditInputError(ValueError):
    """Eine angegebene Datei fehlt oder ist leer; der Text sagt, welche."""


@dataclass(frozen=True)
class WitnessRecord:
    """Ein Kettenkopf aus einer Zeugen-Mail oder Kopf-Datei."""

    source: str
    head: chain.Head
    stamped_at: datetime | None
    token: bytes | None


def witness_text(path: Path) -> str:
    """Text einer gespeicherten Mail (Klartext-Teil) oder einer Kopf-Datei."""
    if path.suffix.lower() == ".eml":
        message = email.message_from_bytes(path.read_bytes(), policy=policy.default)
        assert isinstance(message, EmailMessage)  # noqa: S101 – policy.default liefert das
        body = message.get_body(preferencelist=("plain",))
        return str(body.get_content()) if body is not None else ""
    return path.read_text(encoding="utf-8", errors="replace")


def _token(lines: list[str]) -> bytes | None:
    """Der Base64-Block am Ende (nach dem OpenSSL-Befehl), sonst nichts."""
    start = next((n for n, line in enumerate(lines) if "-CAfile" in line), None)
    if start is None:
        return None
    block: list[str] = []
    for line in lines[start + 1 :]:
        if not line.strip():
            if block:
                break
            continue
        if not _BASE64.match(line.strip()):
            break
        block.append(line.strip())
    try:
        return base64.b64decode("".join(block), validate=True) if block else None
    except binascii.Error:
        return None


def parse_witness(text: str, source: str) -> WitnessRecord | None:
    """Liest Nummer, Hash, Zeit und Stempel aus dem Text von `witness.report()`."""
    seq, digest = _SEQ.search(text), _HASH.search(text)
    if seq is None or digest is None:
        return None
    time = _TIME.search(text)
    stamped_at = (
        datetime.strptime(time.group(1), "%d.%m.%Y %H:%M:%S").replace(tzinfo=UTC) if time else None
    )
    return WitnessRecord(
        source=source,
        head=chain.Head(int(seq.group(1)), digest.group(1)),
        stamped_at=stamped_at,
        token=_token(text.splitlines()),
    )


def witness_files(paths: Iterable[str]) -> list[Path]:
    """Dateien und Ordner (rekursiv) mit Zeugen-Mails bzw. Kopf-Dateien."""
    files: list[Path] = []
    for name in paths:
        path = Path(name)
        if path.is_dir():
            found = sorted(
                p for p in path.rglob("*") if p.is_file() and p.suffix.lower() in ZEUGEN_SUFFIXES
            )
            if not found:
                raise AuditInputError(f"Im Ordner {path} liegen keine .eml- oder .txt-Dateien.")
            files += found
        elif path.is_file():
            files.append(path)
        else:
            raise AuditInputError(f"{path} gibt es nicht.")
    return files


def read_fingerprints(path: str) -> set[str]:
    """SHA-256-Fingerabdrücke aus einer Textdatei; `#` leitet Kommentare ein."""
    try:
        lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as error:
        raise AuditInputError(f"{path} lässt sich nicht lesen.") from error
    found = {
        match.group(0).replace(":", "").lower()
        for line in lines
        for match in _FINGERPRINT.finditer(line.partition("#")[0])
    }
    if not found:
        raise AuditInputError(f"In {path} steht kein SHA-256-Fingerabdruck.")
    return found


def _check_witnesses(records: list[WitnessRecord], result: CheckResult) -> None:
    stamped = 0
    for record in records:
        where = f"Zeugen-Datei {record.source} (Kettenkopf {record.head.seq})"
        if record.token is None:
            if record.stamped_at is not None:
                result.unchecked.append(f"{where}: Der Zeitstempel-Block fehlt in der Datei.")
            continue
        try:
            at = timestamp.check(record.token, bytes.fromhex(record.head.hash))
        except ImproperlyConfigured as error:
            result.unchecked.append(f"Zeugen-Dateien: Zeitstempel nicht prüfbar: {error}")
            return
        except timestamp.TimestampError as error:
            result.findings.append(f"{where}: {error}")
            continue
        if record.stamped_at is not None and at.replace(microsecond=0) != record.stamped_at:
            result.findings.append(f"{where}: Die angegebene Zeit weicht vom Zeitstempel ab.")
            continue
        stamped += 1
    result.confirmed.append(
        f"Zeugen-Dateien: {len(records)} Kettenköpfe gelesen, {stamped} mit gültigem Zeitstempel"
    )


def _signed_heads() -> list[chain.Head]:
    """Die mitsignierten Kettenköpfe aus den Prüfberichten (aus dem PDF, nicht aus Spalten)."""
    from inspections.models import SignedProtocol

    signed = SignedProtocol.objects.select_related("protocol")
    return [anchor.head for entry in signed if (anchor := reports.anchor_of(entry)) is not None]


def _check_components(result: CheckResult) -> None:
    """Jedes Bauteil passt zu seinem letzten festgehaltenen Stand (#20)."""
    from inventory.models import Component
    from inventory.services import component_problems

    problems = component_problems()
    result.findings.extend(f"Stammdaten: {problem}" for problem in problems)
    if not problems:
        result.confirmed.append(
            f"Stammdaten: alle {Component.objects.count()} Bauteile wie festgehalten"
        )


def _check_reports(result: CheckResult) -> None:
    from inspections.models import SignedProtocol
    from legacy.models import LegacyReport

    signed = SignedProtocol.objects.select_related(
        "round", "protocol", "certificate", "round__inspector"
    ).order_by("signed_at", "pk")
    unchanged = 0
    for entry in signed:
        report = reports.check(entry, links=False)
        result.findings.extend(f"Prüfbericht {entry.round}: {f}" for f in report.findings)
        for reason in report.unchecked:
            if f"Prüfberichte: {reason}" not in result.unchecked:
                result.unchecked.append(f"Prüfberichte: {reason}")
        unchanged += report.verdict == reports.Verdict.UNCHANGED
    result.confirmed.append(f"Prüfberichte: {unchanged} von {len(signed)} vollständig unverändert")
    legacy = LegacyReport.objects.count()
    if legacy:
        result.notes.append(
            f"{legacy} Altberichte aus dem Altsystem sind ungesichert, ohne Prüfung."
        )


def _check_fingerprints(listed: set[str], result: CheckResult) -> None:
    from accounts.models import SigningCertificate

    entries = list(SigningCertificate.objects.order_by("created_at", "pk"))
    missing = [entry for entry in entries if entry.sha256 not in listed]
    result.findings.extend(
        f"Zertifikat „{entry.name}“ ({entry.sha256}) steht nicht auf der Liste der Fingerabdrücke."
        for entry in missing
    )
    if not missing:
        result.confirmed.append(f"Alle {len(entries)} Signaturzertifikate stehen auf der Liste")
    absent = len(listed - {entry.sha256 for entry in entries})
    if absent:
        result.notes.append(f"{absent} Fingerabdruck/-drücke der Liste fehlen in PSAgent.")


def run(
    *,
    heads: Iterable[chain.Head] = (),
    witness_paths: Iterable[str] = (),
    fingerprints: str | None = None,
) -> CheckResult:
    """Prüft alles; Dateifehler melden sich vorab als `AuditInputError`."""
    records = []
    unreadable = []
    for path in witness_files(witness_paths):
        try:
            record = parse_witness(witness_text(path), path.name)
        except (OSError, ValueError):
            record = None
        if record is None:
            unreadable.append(f"Zeugen-Datei {path.name}: kein Kettenkopf gefunden.")
        else:
            records.append(record)
    listed = read_fingerprints(fingerprints) if fingerprints else None

    result = CheckResult(unchecked=unreadable)
    # Derselbe Kopf kommt oft mehrfach vor (Mail, Datei, Spalte, PDF); jede Meldung nur einmal.
    known = list(
        dict.fromkeys(
            [*heads, *(r.head for r in records), *witness.known_heads(), *_signed_heads()]
        )
    )
    problems = chain.verify(known)
    result.findings.extend(f"Hash-Kette: {problem}" for problem in problems)
    if not problems:
        current = chain.head()
        result.confirmed.append(
            f"Kette unverändert ({current.seq if current else 0} Glieder, "
            f"{len(known)} bekannte Köpfe enthalten)"
        )
    try:
        stamps = witness.check_stamps()
    except ImproperlyConfigured as error:
        result.unchecked.append(f"Festgehaltene Köpfe: Zeitstempel nicht prüfbar: {error}")
    else:
        result.findings.extend(f"Festgehaltene Köpfe: {problem}" for problem in stamps)
    if records:
        _check_witnesses(records, result)
    _check_components(result)
    _check_reports(result)
    if listed is not None:
        _check_fingerprints(listed, result)
    return result
