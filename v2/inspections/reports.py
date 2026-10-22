"""Prüfberichte (Parität 9, #39): Liste, Details und Prüfknopf „unverändert?“.

Festlegungen (Felix, 21.10.2026): sehen dürfen alle Rollen; Berichte aus dem Altsystem stehen in
derselben Liste, markiert als „Altbestand, ungesichert“, ohne Prüfknopf; filtern nach Zeitraum,
Sachkundiger Person und Teil/EI. Der Befehl über die ganze Kette und der Abgleich mit der
Zeugen-Mail gehören zu #19 (`kette_pruefen`).

Der Prüfknopf prüft einen signierten Bericht vollständig neu, unabhängig vom Ergebnis beim
Hochladen:

1. PDF und Prüfprotokoll haben noch ihre gespeicherte Prüfsumme; das signierte PDF beginnt mit
   dem Protokoll.
2. Die Ergebnisse der Runde ergeben noch die Prüfsumme vom Abschluss, und zwar die im signierten
   Protokoll (Schlüsselwörter, `protocol.read_anchor`), nicht nur die in der Datenbank.
3. Die Zeilen des Berichts passen zu ihren Kettengliedern, die Kette ist lückenlos und enthält
   noch den mitsignierten Kettenkopf (und die festgehaltenen Köpfe).
4. Die Signatur ist intakt; nach ihr kam nur der Zeitstempel. Signiert hat das hinterlegte
   Zertifikat, das zum Zeitpunkt des Hochladens gültig war (Rückblick: ohne heutige Rolle).
5. Der Zeitstempel ist gültig, stammt vom konfigurierten Dienst und trägt die gespeicherte Zeit.

Ergebnis: unverändert, verändert (was und wo) oder nicht prüfbar (warum). Alles Kryptografische
übernehmen pyHanko und `cryptography` (CLAUDE.md, Regel 6).
"""

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from io import BytesIO

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db.models import Q
from pyhanko.pdf_utils.reader import PdfFileReader
from pyhanko.sign.diff_analysis import ModificationLevel
from pyhanko.sign.validation import validate_pdf_signature, validate_pdf_timestamp
from pyhanko_certvalidator import ValidationContext

from accounts import certificates
from core import chain, timestamp, witness
from inspections.models import (
    InspectionResult,
    RoundEvent,
    RoundEventKind,
    RoundProtocol,
    SignedProtocol,
)
from inspections.protocol import Anchor, read_anchor
from inventory.models import normalize_ei
from legacy.models import LegacyReport

# So viele Berichte zeigt die Liste höchstens (neueste zuerst).
LIST_LIMIT = 200


class Verdict(StrEnum):
    UNCHANGED = "unverändert"
    CHANGED = "verändert"
    UNCHECKABLE = "nicht prüfbar"


@dataclass
class CheckResult:
    """Ergebnis des Prüfknopfs; `findings` nennt Veränderungen, `unchecked` Gründe.

    `notes` sind Hinweise ohne Einfluss auf das Ergebnis (Prüfwerkzeug, #19).
    """

    findings: list[str] = field(default_factory=list)
    unchecked: list[str] = field(default_factory=list)
    confirmed: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def verdict(self) -> Verdict:
        if self.findings:
            return Verdict.CHANGED
        return Verdict.UNCHECKABLE if self.unchecked else Verdict.UNCHANGED


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _check_files(signed: SignedProtocol, result: CheckResult) -> bytes | None:
    pdf, protocol = bytes(signed.pdf), signed.protocol
    original = bytes(protocol.pdf)
    if _sha256(pdf) != signed.sha256:
        result.findings.append("Das signierte PDF wurde in der Datenbank verändert.")
    if _sha256(original) != protocol.sha256:
        result.findings.append("Das Prüfprotokoll wurde in der Datenbank verändert.")
    if not pdf.startswith(original):
        result.findings.append("Das signierte PDF passt nicht zum Prüfprotokoll der Runde.")
        return None
    result.confirmed.append("PDF und Prüfprotokoll unverändert")
    return pdf


def anchor_of(signed: SignedProtocol) -> Anchor | None:
    """Die mitsignierten Werte; nur aus dem Teil des PDFs, den die Signatur abdeckt."""
    pdf, original = bytes(signed.pdf), bytes(signed.protocol.pdf)
    return read_anchor(original) if pdf.startswith(original) else None


def _check_anchor(signed: SignedProtocol, result: CheckResult) -> Anchor | None:
    protocol = signed.protocol
    anchor = anchor_of(signed)
    if anchor is None:
        result.unchecked.append("Das Protokoll enthält keine mitsignierten Prüfwerte.")
        return None
    if anchor.round_id != signed.round_id:
        result.findings.append(f"Das signierte Protokoll gehört zu Runde {anchor.round_id}.")
    if (anchor.content_sha256, anchor.head) != (
        protocol.content_sha256,
        chain.Head(protocol.chain_seq, protocol.chain_hash),
    ):
        result.findings.append("Die gespeicherten Werte des Protokolls weichen vom signierten ab.")
    return anchor


def _check_content(signed: SignedProtocol, result: CheckResult, anchor: Anchor | None) -> None:
    from inspections.services import content_sha256

    round_ = signed.round
    closed = RoundEvent.objects.filter(round=round_, kind=RoundEventKind.CLOSED).first()
    digest = content_sha256(round_)
    if (
        closed is None
        or digest != closed.content_sha256
        or digest != signed.protocol.content_sha256
        or (anchor is not None and digest != anchor.content_sha256)
    ):
        result.findings.append("Die Ergebnisse der Runde passen nicht mehr zum Abschluss.")
    else:
        result.confirmed.append("Ergebnisse der Runde wie beim Abschluss")


def _check_chain(
    signed: SignedProtocol, result: CheckResult, anchor: Anchor | None, *, links: bool
) -> None:
    round_ = signed.round
    problems = (
        chain.verify_rows(SignedProtocol, [signed.pk])
        + chain.verify_rows(RoundProtocol, [signed.protocol_id])
        + chain.verify_rows(RoundEvent, round_.events.values_list("pk", flat=True))
        + chain.verify_rows(InspectionResult, round_.results.values_list("pk", flat=True))
    )
    if links:
        head = chain.Head(signed.protocol.chain_seq, signed.protocol.chain_hash)
        signed_heads = [anchor.head] if anchor is not None else []
        problems += chain.verify_links([head, *signed_heads, *witness.known_heads()])
    if problems:
        result.findings.extend(f"Hash-Kette: {problem}" for problem in problems)
    elif links:
        result.confirmed.append(
            f"Hash-Kette lückenlos, Kettenkopf {signed.protocol.chain_seq} enthalten"
        )
    else:
        result.confirmed.append("Zeilen des Berichts passen zur Hash-Kette")


def _check_signature(signed: SignedProtocol, pdf: bytes, result: CheckResult) -> None:
    try:
        reader = PdfFileReader(BytesIO(pdf))
        signatures = reader.embedded_regular_signatures
        stamps = reader.embedded_timestamp_signatures
    except Exception:
        result.findings.append("Das signierte PDF ist nicht mehr lesbar.")
        return
    if len(signatures) != 1 or len(stamps) != 1:
        result.findings.append("Das PDF hat nicht genau eine Signatur und einen Zeitstempel.")
        return
    signature = signatures[0]
    if stamps[0].signed_revision <= signature.signed_revision:
        result.findings.append("Der Zeitstempel liegt vor der Signatur und deckt sie nicht ab.")
        return
    signer = signature.signer_cert
    if _sha256(signer.dump()) != signed.certificate.sha256:
        result.findings.append("Signiert hat ein anderes als das hinterlegte Zertifikat.")
        return
    try:
        status = validate_pdf_signature(
            signature, ValidationContext(trust_roots=[signer], allow_fetching=False)
        )
    except Exception:
        result.findings.append("Die Signatur lässt sich nicht mehr prüfen.")
        return
    if not (status.intact and status.valid):
        result.findings.append("Die Signatur passt nicht mehr zum Inhalt.")
        return
    level = status.modification_level
    if level is None or level > ModificationLevel.LTA_UPDATES:
        result.findings.append(
            "Nach der Signatur wurde das PDF über den Zeitstempel hinaus geändert."
        )
        return
    result.confirmed.append("Signatur intakt, danach nur der Zeitstempel")
    _check_certificate(signed, result)
    _check_stamp(signed, stamps[0], result)


def _check_certificate(signed: SignedProtocol, result: CheckResult) -> None:
    if not str(settings.PSAGENT_YUBICO_CA_PATH or "").strip():
        result.unchecked.append("Zertifikat: Wurzel von Yubico nicht eingerichtet.")
        return
    try:
        entry = certificates.signer_at(
            bytes(signed.certificate.der), signed.signed_at, looking_back=True
        )
    except certificates.CertificateError as error:
        result.findings.append(f"Zertifikat: {error}")
        return
    if entry.user_id != signed.round.inspector_id:
        result.findings.append("Das Zertifikat gehört nicht zum Prüfer der Runde.")
        return
    result.confirmed.append(
        f"Zertifikat „{entry.name}“ war beim Hochladen hinterlegt, Sachkunde gültig"
    )


def _check_stamp(signed: SignedProtocol, stamp: object, result: CheckResult) -> None:
    try:
        roots = timestamp.trust_roots()
    except ImproperlyConfigured as error:
        result.unchecked.append(f"Zeitstempel: {error}")
        return
    try:
        status = validate_pdf_timestamp(
            stamp,  # type: ignore[arg-type]
            validation_context=ValidationContext(trust_roots=roots, allow_fetching=False),
        )
    except Exception:
        result.findings.append("Der Zeitstempel lässt sich nicht mehr prüfen.")
        return
    if not (status.intact and status.valid and status.trusted):
        result.findings.append("Der Zeitstempel ist ungültig oder stammt nicht vom Dienst.")
        return
    if abs(status.timestamp - signed.stamped_at) > timedelta(seconds=1):
        result.findings.append("Die gespeicherte Zeit weicht vom Zeitstempel ab.")
        return
    result.confirmed.append(f"Zeitstempel gültig ({status.timestamp:%d.%m.%Y %H:%M} UTC)")


def check(signed: SignedProtocol, *, links: bool = True) -> CheckResult:
    """Prüfknopf „unverändert?“ für einen signierten Prüfbericht.

    `links=False`: Die Glieder der ganzen Kette prüft der Aufrufer selbst (Prüfwerkzeug, #19).
    """
    result = CheckResult()
    pdf = _check_files(signed, result)
    anchor = _check_anchor(signed, result) if pdf is not None else None
    _check_content(signed, result, anchor)
    _check_chain(signed, result, anchor, links=links)
    if pdf is not None:
        _check_signature(signed, pdf, result)
    return result


# --- Liste ---


@dataclass(frozen=True)
class Entry:
    """Eine Zeile der Liste: signierter Bericht oder Altbericht."""

    legacy: bool
    pk: int
    at: datetime | None
    label: str
    inspector: str
    parts: int


def signed_reports(
    *, start: date | None = None, end: date | None = None, inspector: str = "", part: str = ""
) -> list[Entry]:
    query = SignedProtocol.objects.select_related("round", "round__inspector")
    if start:
        query = query.filter(signed_at__date__gte=start)
    if end:
        query = query.filter(signed_at__date__lte=end)
    if inspector:
        query = query.filter(
            Q(round__inspector__first_name__icontains=inspector)
            | Q(round__inspector__last_name__icontains=inspector)
            | Q(round__inspector__username__icontains=inspector)
        )
    if part:
        query = query.filter(
            Q(round__results__part__components__ei_key=normalize_ei(part))
            | Q(round__results__part__name__icontains=part)
        ).distinct()
    return [
        Entry(
            legacy=False,
            pk=signed.round_id,
            at=signed.signed_at,
            label=str(signed.round),
            inspector=signed.round.inspector_display,
            parts=signed.round.current_results().count(),
        )
        for signed in query.order_by("-signed_at")[:LIST_LIMIT]
    ]


def legacy_reports(
    *, start: date | None = None, end: date | None = None, inspector: str = "", part: str = ""
) -> list[Entry]:
    query = LegacyReport.objects.all()
    if start:
        query = query.filter(created_at__date__gte=start)
    if end:
        query = query.filter(created_at__date__lte=end)
    if inspector:
        query = query.filter(inspector_name__icontains=inspector)
    if part:
        query = query.filter(
            Q(items__part__components__ei_key=normalize_ei(part))
            | Q(items__item_label__icontains=part)
        ).distinct()
    return [
        Entry(
            legacy=True,
            pk=report.pk,
            at=report.created_at,
            label=f"Altbericht #{report.legacy_id}",
            inspector=report.inspector_name or "–",
            parts=report.items.count(),
        )
        for report in query.order_by("-created_at", "-legacy_id")[:LIST_LIMIT]
    ]


def all_reports(**filters: object) -> list[Entry]:
    """Signierte Berichte und Altberichte, neueste zuerst."""
    entries = signed_reports(**filters) + legacy_reports(**filters)  # type: ignore[arg-type]
    oldest = datetime.min.replace(tzinfo=UTC)
    entries.sort(key=lambda entry: entry.at or oldest, reverse=True)
    return entries[:LIST_LIMIT]
