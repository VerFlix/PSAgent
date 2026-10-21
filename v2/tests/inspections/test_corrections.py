"""Korrekturen sind neue Einträge mit Verweis auf den alten (CLAUDE.md, Regel 3)."""

import pytest
from django.db import IntegrityError, transaction

from accounts.models import User
from inspections.models import InspectionResult, Outcome
from inspections.services import (
    InspectionError,
    close_round,
    correct_result,
    next_inspection_due,
    record_result,
    start_round,
)
from inventory.models import PartStatus
from tests.helpers import make_inspector, signed_inspection
from tests.inspections.conftest import MakePart, in_days
from tests.signing import sign


def test_correction_in_open_round_adds_new_entry(make_part: MakePart, inspector: User) -> None:
    """Abnahme #8: Eine Korrektur erzeugt einen neuen Eintrag mit Verweis."""
    part = make_part()
    round_ = start_round(actor=inspector)
    first = record_result(round_, part, Outcome.PASSED, actor=inspector, next_due=in_days(365))

    second = correct_result(
        first, Outcome.DEFECT, actor=inspector, comment="Kantenschaden übersehen"
    )

    assert second.pk != first.pk
    assert second.corrects == first
    assert first.correction == second
    assert InspectionResult.objects.filter(part=part).count() == 2
    assert list(round_.current_results()) == [second]

    close_round(round_, actor=inspector)
    sign(round_, actor=inspector)
    assert part.status() == PartStatus.LOCKED


def test_correction_chain(make_part: MakePart, inspector: User) -> None:
    part = make_part()
    round_ = start_round(actor=inspector)
    first = record_result(round_, part, Outcome.PASSED, actor=inspector, next_due=in_days(365))
    second = correct_result(first, Outcome.PASSED, actor=inspector, next_due=in_days(180))
    third = correct_result(second, Outcome.PASSED, actor=inspector, next_due=in_days(90))
    close_round(round_, actor=inspector)
    sign(round_, actor=inspector)

    assert list(round_.current_results()) == [third]
    assert next_inspection_due(part) == in_days(90)


def test_result_is_corrected_at_most_once(make_part: MakePart, inspector: User) -> None:
    part = make_part()
    round_ = start_round(actor=inspector)
    first = record_result(round_, part, Outcome.PASSED, actor=inspector, next_due=in_days(365))
    correct_result(first, Outcome.PASSED, actor=inspector, next_due=in_days(180))

    with pytest.raises(InspectionError, match="bereits korrigiert"):
        correct_result(first, Outcome.PASSED, actor=inspector, next_due=in_days(90))
    with pytest.raises(IntegrityError), transaction.atomic():
        InspectionResult.objects.create(
            round=round_, part=part, outcome=Outcome.PASSED, next_due=in_days(90), corrects=first
        )


def test_signed_result_is_corrected_in_a_new_round(make_part: MakePart, inspector: User) -> None:
    part = make_part()
    signed = signed_inspection(inspector, [part], next_due=in_days(365))
    old = signed.results.get()
    follow_up = start_round(actor=inspector)

    with pytest.raises(InspectionError, match="neue Runde"):
        correct_result(old, Outcome.PASSED, actor=inspector, next_due=in_days(30))
    correction = correct_result(
        old,
        Outcome.PASSED,
        actor=inspector,
        comment="Datum falsch übertragen",
        next_due=in_days(30),
        round_=follow_up,
    )

    assert (correction.round, correction.corrects) == (follow_up, old)
    assert next_inspection_due(part) == in_days(365)  # wirkt erst mit Signatur

    close_round(follow_up, actor=inspector)
    sign(follow_up, actor=inspector)

    assert next_inspection_due(part) == in_days(30)
    old.refresh_from_db()
    assert old.next_due == in_days(365)  # das alte Ergebnis bleibt unverändert erhalten


def test_closed_unsigned_round_is_not_corrected(make_part: MakePart, inspector: User) -> None:
    part = make_part()
    round_ = start_round(actor=inspector)
    result = record_result(round_, part, Outcome.PASSED, actor=inspector, next_due=in_days(365))
    close_round(round_, actor=inspector)

    with pytest.raises(InspectionError, match="abgeschlossen"):
        correct_result(result, Outcome.PASSED, actor=inspector, next_due=in_days(30))


def test_correction_of_open_round_stays_in_it(make_part: MakePart, inspector: User) -> None:
    part = make_part()
    round_ = start_round(actor=inspector)
    result = record_result(round_, part, Outcome.PASSED, actor=inspector, next_due=in_days(365))

    with pytest.raises(InspectionError, match="in ihr korrigiert"):
        correct_result(
            result,
            Outcome.PASSED,
            actor=inspector,
            next_due=in_days(30),
            # je Person nur eine offene Runde; die zweite gehört jemand anderem
            round_=start_round(actor=make_inspector()),
        )


def test_correction_round_may_not_already_contain_the_part(
    make_part: MakePart, inspector: User
) -> None:
    part = make_part()
    old = signed_inspection(inspector, [part]).results.get()
    follow_up = start_round(actor=inspector)
    record_result(follow_up, part, Outcome.PASSED, actor=inspector, next_due=in_days(200))

    with pytest.raises(InspectionError, match="bereits erfasst"):
        correct_result(old, Outcome.PASSED, actor=inspector, next_due=in_days(30), round_=follow_up)


def test_later_inspection_wins(make_part: MakePart, inspector: User) -> None:
    part = make_part()
    signed_inspection(inspector, [part], next_due=in_days(365))
    signed_inspection(inspector, [part], next_due=in_days(100))

    assert next_inspection_due(part) == in_days(100)
