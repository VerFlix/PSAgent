"""Prüfungsrunde: erfassen, abschließen, signieren, abbrechen (Parität 7, 3).

Ergebnisse wirken erst mit der Signatur auf die Teile. Bis dahin bleibt der Bestand unverändert.
"""

from datetime import date

import pytest
from django.db import IntegrityError, transaction

from accounts.models import User
from inspections.models import (
    InspectionResult,
    Outcome,
    RoundEvent,
    RoundEventKind,
    RoundState,
)
from inspections.services import (
    InspectionError,
    add_months,
    cancel_round,
    close_round,
    content_sha256,
    next_inspection_due,
    record_result,
    start_round,
    suggest_next_due,
)
from inventory.models import EventKind, PartEvent, PartStatus
from inventory.services import record_event, update_component
from tests.helpers import make_inspector, signed_inspection
from tests.inspections.conftest import MakePart, in_days
from tests.signing import sign


def test_full_round_releases_new_parts(make_part: MakePart, inspector: User) -> None:
    """Parität 1 und 7: neue Teile werden erst durch die signierte Prüfung verleihbar."""
    first, second = make_part(), make_part()
    round_ = start_round(actor=inspector)
    record_result(round_, first, Outcome.PASSED, actor=inspector, next_due=in_days(365))
    record_result(round_, second, Outcome.PASSED, actor=inspector, next_due=in_days(180))

    assert round_.state() == RoundState.OPEN
    close_round(round_, actor=inspector)
    assert round_.state() == RoundState.CLOSED
    assert first.status() == PartStatus.NEW  # noch ohne Wirkung
    assert next_inspection_due(first) is None

    sign(round_, actor=inspector)

    assert round_.state() == RoundState.SIGNED
    assert (first.status(), second.status()) == (PartStatus.RELEASED, PartStatus.RELEASED)
    assert (next_inspection_due(first), next_inspection_due(second)) == (in_days(365), in_days(180))
    released = PartEvent.objects.get(part=first, kind=EventKind.RELEASED)
    assert released.actor == inspector
    assert released.inspection_result == InspectionResult.objects.get(part=first)


@pytest.mark.parametrize(
    ("outcome", "comment", "next_due", "status"),
    [
        (Outcome.DEFECT, "Naht beschädigt", 30, PartStatus.LOCKED),
        (Outcome.RETIRE, "Ablegereife erreicht", None, PartStatus.RETIRED),
    ],
)
def test_defect_locks_and_retire_retires(
    outcome: Outcome,
    comment: str,
    next_due: int | None,
    status: PartStatus,
    make_part: MakePart,
    inspector: User,
) -> None:
    part = make_part()
    signed_inspection(inspector, [part])
    due = in_days(next_due) if next_due is not None else None

    signed_inspection(inspector, [part], outcome=outcome, comment=comment, next_due=due)

    assert part.status() == status
    event = part.events.exclude(kind=EventKind.RELEASED).get()
    assert (event.comment, event.actor) == (comment, inspector)
    assert next_inspection_due(part) == due


def test_passed_inspection_unlocks_locked_part(make_part: MakePart, inspector: User) -> None:
    part = make_part()
    signed_inspection(inspector, [part])
    record_event(part, EventKind.LOCKED, actor=inspector, comment="nach Sturz zur Prüfung")

    signed_inspection(inspector, [part], comment="Nachprüfung ohne Befund")

    assert part.status() == PartStatus.RELEASED
    unlocked = part.events.get(kind=EventKind.UNLOCKED)
    assert "Nachprüfung ohne Befund" in unlocked.comment
    assert unlocked.inspection_result is not None


def test_defect_on_locked_part_keeps_single_lock(make_part: MakePart, inspector: User) -> None:
    part = make_part()
    record_event(part, EventKind.LOCKED, actor=inspector, comment="Rückruf")

    signed_inspection(inspector, [part], outcome=Outcome.DEFECT, comment="Rückruf bestätigt")

    assert part.events.filter(kind=EventKind.LOCKED).count() == 1
    assert part.status() == PartStatus.LOCKED


def test_cancelled_round_has_no_effect(make_part: MakePart, inspector: User) -> None:
    part = make_part()
    round_ = start_round(actor=inspector)
    record_result(round_, part, Outcome.RETIRE, actor=inspector, comment="aussondern?")
    close_round(round_, actor=inspector)

    cancel_round(round_, actor=inspector, comment="falsches Teil gescannt")

    assert round_.state() == RoundState.CANCELLED
    assert part.status() == PartStatus.NEW
    with pytest.raises(InspectionError, match="abgebrochen"):
        sign(round_, actor=inspector)


@pytest.mark.parametrize("comment", ["", "  "])
def test_cancel_needs_comment(comment: str, make_part: MakePart, inspector: User) -> None:
    round_ = start_round(actor=inspector)

    with pytest.raises(InspectionError, match="Kommentar"):
        cancel_round(round_, actor=inspector, comment=comment)
    with pytest.raises(IntegrityError), transaction.atomic():
        RoundEvent.objects.create(round=round_, kind=RoundEventKind.CANCELLED, comment=comment)


def test_signed_round_cannot_be_cancelled_or_signed_again(
    make_part: MakePart, inspector: User
) -> None:
    round_ = signed_inspection(inspector, [make_part()])

    with pytest.raises(InspectionError, match="signiert"):
        cancel_round(round_, actor=inspector, comment="doch nicht")
    with pytest.raises(InspectionError, match="signiert"):
        sign(round_, actor=inspector)
    with pytest.raises(IntegrityError), transaction.atomic():
        RoundEvent.objects.create(round=round_, kind=RoundEventKind.SIGNED)


def test_open_round_cannot_be_signed(make_part: MakePart, inspector: User) -> None:
    round_ = start_round(actor=inspector)
    record_result(round_, make_part(), Outcome.PASSED, actor=inspector, next_due=in_days(365))

    with pytest.raises(InspectionError, match="offen"):
        sign(round_, actor=inspector)


def test_empty_round_cannot_be_closed(inspector: User) -> None:
    with pytest.raises(InspectionError, match="keine Ergebnisse"):
        close_round(start_round(actor=inspector), actor=inspector)


def test_closed_round_is_frozen(make_part: MakePart, inspector: User) -> None:
    first, second = make_part(), make_part()
    round_ = start_round(actor=inspector)
    record_result(round_, first, Outcome.PASSED, actor=inspector, next_due=in_days(365))
    close_round(round_, actor=inspector)

    with pytest.raises(InspectionError, match="abgeschlossen"):
        record_result(round_, second, Outcome.PASSED, actor=inspector, next_due=in_days(365))


def test_close_stores_content_hash(make_part: MakePart, inspector: User) -> None:
    """Vorbereitet für Signatur und Hash-Kette (Phase 3)."""
    round_ = start_round(actor=inspector)
    record_result(round_, make_part(), Outcome.PASSED, actor=inspector, next_due=in_days(365))

    event = close_round(round_, actor=inspector)

    assert event.content_sha256 == content_sha256(round_)
    assert len(event.content_sha256) == 64


def test_close_needs_hash_in_database(make_part: MakePart, inspector: User) -> None:
    round_ = start_round(actor=inspector)

    with pytest.raises(IntegrityError), transaction.atomic():
        RoundEvent.objects.create(round=round_, kind=RoundEventKind.CLOSED, content_sha256="")
    with pytest.raises(IntegrityError), transaction.atomic():
        RoundEvent.objects.create(round=round_, kind=RoundEventKind.SIGNED, content_sha256="0" * 64)


def test_changed_content_after_close_blocks_signing(make_part: MakePart, inspector: User) -> None:
    """Ändert sich die EI zwischen Abschluss und Signatur, passt der Inhalt nicht mehr."""
    part = make_part()
    round_ = start_round(actor=inspector)
    record_result(round_, part, Outcome.PASSED, actor=inspector, next_due=in_days(365))
    close_round(round_, actor=inspector)
    update_component(part.components.get(), actor=inspector, ei="TST-GEAENDERT")

    with pytest.raises(InspectionError, match="geändert"):
        sign(round_, actor=inspector)

    assert part.status() == PartStatus.NEW


# --- Ergebnisse: Pflichtangaben ---


@pytest.mark.parametrize(
    ("outcome", "comment", "next_due", "message"),
    [
        (Outcome.DEFECT, "", 30, "Kommentar"),
        (Outcome.RETIRE, " ", None, "Kommentar"),
        (Outcome.PASSED, "", None, "nächsten Prüfung"),
        (Outcome.RETIRE, "kaputt", 30, "keine nächste Prüfung"),
        (Outcome.PASSED, "", 0, "Zukunft"),
        (Outcome.PASSED, "", -10, "Zukunft"),
        ("bestanden", "", 30, "Unbekanntes Ergebnis"),
    ],
)
def test_result_needs_valid_fields(
    outcome: str,
    comment: str,
    next_due: int | None,
    message: str,
    make_part: MakePart,
    inspector: User,
) -> None:
    round_ = start_round(actor=inspector)
    due = in_days(next_due) if next_due is not None else None

    with pytest.raises(InspectionError, match=message):
        record_result(round_, make_part(), outcome, actor=inspector, comment=comment, next_due=due)

    assert not InspectionResult.objects.exists()


@pytest.mark.parametrize(
    ("outcome", "comment", "next_due"),
    [
        (Outcome.DEFECT, "", date(2027, 1, 1)),
        (Outcome.PASSED, "", None),
        (Outcome.RETIRE, "kaputt", date(2027, 1, 1)),
        ("bestanden", "", date(2027, 1, 1)),
    ],
)
def test_database_checks_result_fields(
    outcome: str, comment: str, next_due: date | None, make_part: MakePart, inspector: User
) -> None:
    round_ = start_round(actor=inspector)

    with pytest.raises(IntegrityError), transaction.atomic():
        InspectionResult.objects.create(
            round=round_, part=make_part(), outcome=outcome, comment=comment, next_due=next_due
        )


def test_part_only_once_per_round(make_part: MakePart, inspector: User) -> None:
    part = make_part()
    round_ = start_round(actor=inspector)
    record_result(round_, part, Outcome.PASSED, actor=inspector, next_due=in_days(365))

    with pytest.raises(InspectionError, match="bereits erfasst"):
        record_result(round_, part, Outcome.PASSED, actor=inspector, next_due=in_days(30))
    with pytest.raises(IntegrityError), transaction.atomic():
        InspectionResult.objects.create(
            round=round_, part=part, outcome=Outcome.PASSED, next_due=in_days(30)
        )


def test_retired_part_cannot_be_inspected(make_part: MakePart, inspector: User) -> None:
    part = make_part()
    signed_inspection(inspector, [part], outcome=Outcome.RETIRE, comment="Ablegereife")

    with pytest.raises(InspectionError, match="ausgesondert"):
        signed_inspection(inspector, [part])


def test_part_retired_before_signing_aborts_whole_round(
    make_part: MakePart, inspector: User
) -> None:
    """Abbruch mitten im Abschluss: Die Wirkung auf das erste Teil wird zurückgenommen."""
    first, second = make_part(), make_part()
    round_ = start_round(actor=inspector)
    record_result(round_, first, Outcome.PASSED, actor=inspector, next_due=in_days(365))
    record_result(round_, second, Outcome.PASSED, actor=inspector, next_due=in_days(365))
    close_round(round_, actor=inspector)
    record_event(second, EventKind.RETIRED, actor=inspector, comment="inzwischen ausgesondert")

    with pytest.raises(ValueError, match="ausgesondert"):
        sign(round_, actor=inspector)

    assert round_.state() == RoundState.CLOSED
    assert first.status() == PartStatus.NEW
    assert not PartEvent.objects.filter(inspection_result__isnull=False).exists()


def test_failure_in_the_middle_of_signing_leaves_nothing(
    make_part: MakePart, inspector: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Abnahme #8: Ein Abbruch mitten im Abschluss hinterlässt keinen halben Zustand."""
    parts = [make_part(), make_part(), make_part()]
    round_ = start_round(actor=inspector)
    for part in parts:
        record_result(round_, part, Outcome.PASSED, actor=inspector, next_due=in_days(365))
    close_round(round_, actor=inspector)
    calls = []

    def failing_record_event(*args: object, **kwargs: object) -> object:
        calls.append(args)
        if len(calls) == 2:
            raise RuntimeError("Stromausfall (simuliert)")
        return record_event(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr("inspections.services.record_event", failing_record_event)

    with pytest.raises(RuntimeError):
        sign(round_, actor=inspector)

    assert len(calls) == 2
    assert round_.state() == RoundState.CLOSED
    assert not RoundEvent.objects.filter(kind=RoundEventKind.SIGNED).exists()
    assert not PartEvent.objects.exists()
    assert {part.status() for part in parts} == {PartStatus.NEW}


# --- Schnellwahl für „nächste Prüfung“ ---


@pytest.mark.parametrize(
    ("start", "months", "expected"),
    [
        (date(2026, 10, 15), 6, date(2027, 4, 15)),
        (date(2026, 10, 15), 12, date(2027, 10, 15)),
        (date(2026, 8, 31), 6, date(2027, 2, 28)),
        (date(2028, 2, 29), 12, date(2029, 2, 28)),
        (date(2026, 12, 31), 1, date(2027, 1, 31)),
    ],
)
def test_add_months_like_legacy(start: date, months: int, expected: date) -> None:
    """Parität 3: Schnellwahl +6 Monate / +1 Jahr wie `_add_months_iso` im Altsystem."""
    assert add_months(start, months) == expected
    assert suggest_next_due(months, start=start) == expected


def test_only_the_rounds_inspector_acts_in_it(make_part: MakePart, inspector: User) -> None:
    other = make_inspector()
    part = make_part()
    round_ = start_round(actor=inspector)

    with pytest.raises(InspectionError, match="handelt nur"):
        record_result(round_, part, Outcome.PASSED, actor=other, next_due=in_days(365))
    record_result(round_, part, Outcome.PASSED, actor=inspector, next_due=in_days(365))
    with pytest.raises(InspectionError, match="handelt nur"):
        close_round(round_, actor=other)
    close_round(round_, actor=inspector)
    with pytest.raises(InspectionError, match="handelt nur"):
        sign(round_, actor=other)
    with pytest.raises(InspectionError, match="handelt nur"):
        cancel_round(round_, actor=other, comment="fremde Runde")
