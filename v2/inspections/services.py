"""Prüfungsrunde: beginnen, Ergebnisse erfassen und korrigieren, abschließen, signieren.

Ablauf (Parität 7):

1. `start_round` – nur mit gültiger Sachkunde und der Rolle PSA-Sachkundig.
2. `record_result` je Teil; `correct_result` legt bei Korrekturen ein neues Ergebnis mit Verweis
   auf das alte an.
3. `close_round` friert den Inhalt ein und hält seine SHA-256-Prüfsumme fest.
4. `sign_round` wirkt auf die Teile, alles in einer Transaktion. Bis Phase 3 ist das ein
   Platzhalter: Später ruft ihn die Signaturprüfung (YubiKey, #16/#18) erst nach erfolgreicher
   Prüfung der Signatur auf. Altbestand übernimmt `adopt_legacy_round` statt einer Signatur.

`cancel_round` bricht eine noch nicht signierte Runde ab; sie wirkt dann nie.
`next_inspection_due` liefert „nächste Prüfung“ eines Teils aus der letzten wirksamen Prüfung;
anders lässt sie sich nicht setzen (Parität 3).
"""

import calendar
import hashlib
import json
from datetime import UTC, date, datetime

from django.db import transaction
from django.db.models import OuterRef, Subquery
from django.utils import timezone

from accounts.models import User
from accounts.rights import Right, require
from inspections.models import (
    EFFECTIVE_KINDS,
    OUTCOMES_WITH_COMMENT,
    InspectionResult,
    InspectionRound,
    Outcome,
    RoundEvent,
    RoundEventKind,
    RoundPart,
    RoundState,
)
from inventory.models import Component, EventKind, Part, normalize_ei
from inventory.services import record_event


class InspectionError(ValueError):
    """Ein Prüfvorgang verstößt gegen eine Fachregel."""


# --- Hilfen ---


def add_months(day: date, months: int) -> date:
    """Datum plus Monate; am Monatsende auf den letzten Tag gekürzt (wie im Altsystem)."""
    index = day.month - 1 + months
    year, month = day.year + index // 12, index % 12 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def suggest_next_due(months: int = 12, *, start: date | None = None) -> date:
    """Vorschlag für „nächste Prüfung“: Schnellwahl +6 Monate oder +1 Jahr (Parität 3)."""
    return add_months(start or timezone.localdate(), months)


def _lock(round_: InspectionRound) -> InspectionRound:
    """Sperrt die Zeile der Runde, damit Vorgänge an ihr nacheinander geprüft werden."""
    return InspectionRound.objects.select_for_update().get(pk=round_.pk)


def _require_inspector(round_: InspectionRound, actor: User | None) -> None:
    """Nur die sachkundige Person der Runde handelt in ihr, und nur mit gültiger Sachkunde."""
    if round_.inspector_id is None:
        if actor is not None:
            raise InspectionError(f"{round_} ist eine Übernahme aus dem Altsystem.")
        return
    if actor is None or actor.pk != round_.inspector_id:
        raise InspectionError(f"In {round_} handelt nur {round_.inspector_display}.")
    require(actor, Right.INSPECT)


def _require_state(round_: InspectionRound, *allowed: RoundState) -> None:
    state = round_.state()
    if state not in allowed:
        raise InspectionError(f"{round_} ist {state.label}.")


def _validate(
    part: Part, outcome: str, comment: str, next_due: date | None, *, actor: User | None
) -> Outcome:
    try:
        result = Outcome(outcome)
    except ValueError as error:
        raise InspectionError(f"Unbekanntes Ergebnis: {outcome!r}") from error
    if part.is_retired():
        raise InspectionError(f"{part} ist ausgesondert.")
    if result in OUTCOMES_WITH_COMMENT and not comment.strip():
        raise InspectionError(f"„{result.label}“ braucht einen Kommentar.")
    if result == Outcome.PASSED and next_due is None:
        raise InspectionError("„in Ordnung“ braucht das Datum der nächsten Prüfung.")
    if result == Outcome.RETIRE and next_due is not None:
        raise InspectionError("Ein ausgesondertes Teil hat keine nächste Prüfung.")
    # Bei der Übernahme aus dem Altsystem darf die nächste Prüfung schon verstrichen sein.
    if next_due is not None and actor is not None and next_due <= timezone.localdate():
        raise InspectionError("Die nächste Prüfung muss in der Zukunft liegen.")
    return result


# --- Runde und Ergebnisse ---


def start_round(
    *,
    actor: User | None,
    inspector_name: str = "",
    legacy_report_id: int | None = None,
    at: datetime | None = None,
) -> InspectionRound:
    """Beginnt eine Prüfungsrunde. Ohne `actor` nur zur Übernahme aus dem Altsystem."""
    if actor is None:
        if not inspector_name.strip():
            raise InspectionError("Bei der Übernahme fehlt der Name der sachkundigen Person.")
        return InspectionRound.objects.create(
            inspector_name=inspector_name.strip(),
            legacy_report_id=legacy_report_id,
            started_at=at or timezone.now(),
        )
    require(actor, Right.INSPECT)
    if legacy_report_id is not None:
        raise InspectionError("Ein Prüfbericht des Altsystems wird nur übernommen.")
    with transaction.atomic():
        # Je Sachkundigem eine offene Runde; die Sperre reiht gleichzeitige Starts ein.
        User.objects.select_for_update().filter(pk=actor.pk).first()
        if open_round(actor) is not None:
            raise InspectionError(
                "Es gibt bereits eine offene Prüfungsrunde; bitte abschließen oder abbrechen."
            )
        return InspectionRound.objects.create(inspector=actor, started_at=at or timezone.now())


def open_round(actor: User) -> InspectionRound | None:
    """Die offene Runde der sachkundigen Person (ohne Abschluss, Signatur oder Abbruch)."""
    return (
        InspectionRound.objects.filter(inspector=actor, events__isnull=True)
        .order_by("-started_at", "-id")
        .first()
    )


# --- Prüfmodus: Teile sammeln (Parität 7) ---


def find_part(ei: str) -> Part:
    """Teil zur EI (ohne Groß-/Kleinschreibung und Leerzeichen); wie das Ziel des QR-Codes."""
    component = Component.objects.select_related("part").filter(ei_key=normalize_ei(ei)).first()
    if component is None:
        raise InspectionError(f"EI „{ei.strip()}“ ist unbekannt.")
    if component.part.is_retired():
        raise InspectionError(f"{component.part} ist ausgesondert.")
    return component.part


@transaction.atomic
def collect(round_: InspectionRound, part: Part, *, actor: User | None) -> RoundPart:
    """Setzt ein Teil auf die Liste der offenen Runde; doppelt gescannt bleibt es einmal."""
    round_ = _lock(round_)
    _require_inspector(round_, actor)
    _require_state(round_, RoundState.OPEN)
    if part.is_retired():
        raise InspectionError(f"{part} ist ausgesondert.")
    entry, _ = RoundPart.objects.get_or_create(round=round_, part=part)
    return entry


def collect_by_ei(round_: InspectionRound, ei: str, *, actor: User | None) -> Part:
    part = find_part(ei)
    collect(round_, part, actor=actor)
    return part


@transaction.atomic
def drop(round_: InspectionRound, part: Part, *, actor: User | None) -> None:
    """Nimmt ein falsch gescanntes Teil wieder von der Liste, solange es kein Ergebnis hat."""
    round_ = _lock(round_)
    _require_inspector(round_, actor)
    _require_state(round_, RoundState.OPEN)
    if round_.results.filter(part=part).exists():
        raise InspectionError(f"{part} hat schon ein Ergebnis; bitte das Ergebnis ändern.")
    RoundPart.objects.filter(round=round_, part=part).delete()


def pending_parts(round_: InspectionRound) -> list[Part]:
    """Teile auf der Liste, die noch kein Ergebnis haben, in der Reihenfolge des Scans."""
    done = round_.results.values("part_id")
    return [
        entry.part for entry in round_.collected.exclude(part_id__in=done).select_related("part")
    ]


@transaction.atomic
def set_result(
    round_: InspectionRound,
    part: Part,
    outcome: str,
    *,
    actor: User | None,
    comment: str = "",
    next_due: date | None = None,
) -> InspectionResult:
    """Ergebnis im Prüfmodus: erstmals erfassen oder, falls schon vorhanden, korrigieren."""
    current = round_.current_results().filter(part=part).first()
    if current is None:
        return record_result(round_, part, outcome, actor=actor, comment=comment, next_due=next_due)
    return correct_result(current, outcome, actor=actor, comment=comment, next_due=next_due)


@transaction.atomic
def pass_remaining(round_: InspectionRound, *, actor: User | None, next_due: date) -> int:
    """Alle Teile der Liste ohne Ergebnis: „in Ordnung“ mit derselben nächsten Prüfung."""
    round_ = _lock(round_)
    _require_inspector(round_, actor)
    _require_state(round_, RoundState.OPEN)
    parts = pending_parts(round_)
    for part in parts:
        record_result(round_, part, Outcome.PASSED, actor=actor, next_due=next_due)
    return len(parts)


@transaction.atomic
def record_result(
    round_: InspectionRound,
    part: Part,
    outcome: str,
    *,
    actor: User | None,
    comment: str = "",
    next_due: date | None = None,
) -> InspectionResult:
    """Erfasst das Ergebnis für ein Teil. Ein Teil steht höchstens einmal in einer Runde."""
    round_ = _lock(round_)
    _require_inspector(round_, actor)
    _require_state(round_, RoundState.OPEN)
    if round_.results.filter(part=part).exists():
        raise InspectionError(
            f"{part} ist in {round_} bereits erfasst; Änderungen sind Korrekturen."
        )
    checked = _validate(part, outcome, comment, next_due, actor=actor)
    RoundPart.objects.get_or_create(round=round_, part=part)
    return InspectionResult.objects.create(
        round=round_,
        part=part,
        outcome=checked,
        comment=comment.strip(),
        next_due=next_due,
        recorded_by=actor,
    )


@transaction.atomic
def correct_result(
    previous: InspectionResult,
    outcome: str,
    *,
    actor: User | None,
    comment: str = "",
    next_due: date | None = None,
    round_: InspectionRound | None = None,
) -> InspectionResult:
    """Korrigiert ein Ergebnis durch ein neues mit Verweis auf das alte.

    Ist die Runde noch offen, kommt die Korrektur in dieselbe Runde. Ist sie signiert oder
    übernommen, gehört die Korrektur in eine neue, offene Runde (`round_`) und wirkt erst mit
    deren Signatur. Abgeschlossene, noch nicht signierte Runden werden nicht korrigiert, sondern
    abgebrochen und neu erfasst.
    """
    if InspectionResult.objects.filter(corrects=previous).exists():
        raise InspectionError("Dieses Ergebnis ist bereits korrigiert; bitte die Korrektur ändern.")
    original_state = previous.round.state()
    if original_state == RoundState.OPEN:
        if round_ is not None and round_.pk != previous.round_id:
            raise InspectionError("Ergebnisse einer offenen Runde werden in ihr korrigiert.")
        target = previous.round
    elif original_state in (RoundState.SIGNED, RoundState.LEGACY):
        if round_ is None:
            raise InspectionError("Die Korrektur einer signierten Prüfung braucht eine neue Runde.")
        target = round_
    else:
        raise InspectionError(f"{previous.round} ist {original_state.label}.")

    target = _lock(target)
    _require_inspector(target, actor)
    _require_state(target, RoundState.OPEN)
    if target.pk != previous.round_id and target.results.filter(part=previous.part).exists():
        raise InspectionError(f"{previous.part} ist in {target} bereits erfasst.")
    checked = _validate(previous.part, outcome, comment, next_due, actor=actor)
    return InspectionResult.objects.create(
        round=target,
        part=previous.part,
        outcome=checked,
        comment=comment.strip(),
        next_due=next_due,
        corrects=previous,
        recorded_by=actor,
    )


# --- Abschluss, Signatur, Abbruch ---


def _utc(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat()


def round_content(round_: InspectionRound) -> bytes:
    """Kanonischer Inhalt der Runde als JSON (UTF-8, Schlüssel sortiert, ohne Leerraum).

    Grundlage der Prüfsumme beim Abschluss und später von Signatur und Hash-Kette (Phase 3).
    """
    results = [
        {
            "id": result.pk,
            "part": result.part_id,
            "part_name": result.part.name,
            "ei": list(result.part.components.values_list("ei", flat=True)),
            "outcome": result.outcome,
            "comment": result.comment,
            "next_due": result.next_due.isoformat() if result.next_due else None,
            "corrects": result.corrects_id,
            "recorded_at": _utc(result.recorded_at),
            "recorded_by": result.recorded_by_id,
        }
        for result in round_.current_results()
    ]
    content = {
        "round": round_.pk,
        "inspector": round_.inspector_id,
        "inspector_name": round_.inspector_display,
        "started_at": _utc(round_.started_at),
        "legacy_report_id": round_.legacy_report_id,
        "results": results,
    }
    return json.dumps(content, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def content_sha256(round_: InspectionRound) -> str:
    return hashlib.sha256(round_content(round_)).hexdigest()


@transaction.atomic
def close_round(round_: InspectionRound, *, actor: User | None) -> RoundEvent:
    """Schließt die Runde ab: danach keine Ergebnisse oder Korrekturen mehr in ihr."""
    round_ = _lock(round_)
    _require_inspector(round_, actor)
    _require_state(round_, RoundState.OPEN)
    if not round_.results.exists():
        raise InspectionError(f"{round_} enthält keine Ergebnisse.")
    if pending := pending_parts(round_):
        raise InspectionError(
            f"{len(pending)} Teil(e) ohne Ergebnis: bitte erfassen oder von der Liste nehmen."
        )
    return RoundEvent.objects.create(
        round=round_,
        kind=RoundEventKind.CLOSED,
        actor=actor,
        content_sha256=content_sha256(round_),
    )


def _verify_unchanged(round_: InspectionRound) -> None:
    closed = round_.events.get(kind=RoundEventKind.CLOSED)
    if content_sha256(round_) != closed.content_sha256:
        raise InspectionError(
            f"Der Inhalt von {round_} hat sich seit dem Abschluss geändert; bitte abbrechen "
            "und neu erfassen."
        )


def _apply(round_: InspectionRound, *, actor: User | None, at: datetime) -> None:
    """Überträgt die Ergebnisse auf die Teile. Läuft in der Transaktion des Aufrufers."""
    for result in round_.current_results():
        part = result.part

        def effect(kind: EventKind, comment: str, result: InspectionResult = result) -> None:
            record_event(
                result.part, kind, actor=actor, at=at, comment=comment, inspection_result=result
            )

        if result.outcome == Outcome.PASSED:
            if part.is_locked():
                note = f"PSA-Prüfung in Ordnung ({round_})"
                effect(EventKind.UNLOCKED, f"{note}: {result.comment}" if result.comment else note)
            if not part.events.filter(kind=EventKind.RELEASED).exists():
                effect(EventKind.RELEASED, result.comment)
        elif result.outcome == Outcome.DEFECT:
            if not part.is_locked():
                effect(EventKind.LOCKED, result.comment)
        else:
            effect(EventKind.RETIRED, result.comment)


@transaction.atomic
def sign_round(round_: InspectionRound, *, actor: User) -> RoundEvent:
    """Signiert eine abgeschlossene Runde und überträgt die Ergebnisse auf die Teile.

    Platzhalter bis Phase 3: Die echte Signatur (YubiKey, Zeitstempel) prüft der Aufrufer vorher.
    Alles geschieht in einer Transaktion; scheitert ein Teil, bleibt nichts zurück.
    """
    round_ = _lock(round_)
    if round_.inspector_id is None:
        raise InspectionError(f"{round_} ist eine Übernahme aus dem Altsystem.")
    _require_inspector(round_, actor)
    _require_state(round_, RoundState.CLOSED)
    _verify_unchanged(round_)
    moment = timezone.now()
    event = RoundEvent.objects.create(
        round=round_, kind=RoundEventKind.SIGNED, actor=actor, at=moment
    )
    _apply(round_, actor=actor, at=moment)
    return event


@transaction.atomic
def adopt_legacy_round(round_: InspectionRound, *, comment: str = "") -> RoundEvent:
    """Übernimmt eine abgeschlossene Runde aus dem Altsystem ohne Signatur (nur Import, #10)."""
    round_ = _lock(round_)
    if round_.inspector_id is not None:
        raise InspectionError("Nur Runden aus dem Altsystem werden ohne Signatur übernommen.")
    _require_state(round_, RoundState.CLOSED)
    _verify_unchanged(round_)
    moment = timezone.now()
    event = RoundEvent.objects.create(
        round=round_, kind=RoundEventKind.LEGACY, at=moment, comment=comment
    )
    _apply(round_, actor=None, at=moment)
    return event


@transaction.atomic
def cancel_round(round_: InspectionRound, *, actor: User | None, comment: str) -> RoundEvent:
    """Bricht eine noch nicht signierte Runde ab. Ihre Ergebnisse wirken nie."""
    round_ = _lock(round_)
    _require_inspector(round_, actor)
    _require_state(round_, RoundState.OPEN, RoundState.CLOSED)
    if not comment.strip():
        raise InspectionError("Ein Abbruch braucht einen Kommentar.")
    return RoundEvent.objects.create(
        round=round_, kind=RoundEventKind.CANCELLED, actor=actor, comment=comment.strip()
    )


# --- Abfragen ---


def effective_result(part: Part) -> InspectionResult | None:
    """Das zuletzt wirksam gewordene, nicht korrigierte Prüfergebnis eines Teils."""
    effective = RoundEvent.objects.filter(kind__in=EFFECTIVE_KINDS)
    rounds = effective.values("round_id")
    return (
        InspectionResult.objects.filter(part=part, round_id__in=rounds)
        .exclude(correction__round_id__in=rounds)
        .annotate(
            effective_at=Subquery(effective.filter(round_id=OuterRef("round_id")).values("at")[:1])
        )
        .order_by("-effective_at", "-id")
        .first()
    )


def next_inspection_due(part: Part) -> date | None:
    """„Nächste Prüfung“ aus der letzten wirksamen Prüfung; None, wenn es keine gibt."""
    result = effective_result(part)
    return result.next_due if result is not None else None
