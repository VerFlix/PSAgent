"""Übernahme von Prüfungen aus dem Altsystem: ohne Konto, ohne Signatur, als solche markiert."""

import pytest
from django.db import IntegrityError, transaction

from accounts.models import User
from inspections.models import InspectionRound, Outcome, RoundState
from inspections.services import (
    InspectionError,
    adopt_legacy_round,
    close_round,
    next_inspection_due,
    record_result,
    sign_round,
    start_round,
)
from inventory.models import Part, PartStatus
from tests.inspections.conftest import MakePart, in_days


def legacy_round(part_due: list[tuple[Part, int]]) -> InspectionRound:
    round_ = start_round(actor=None, inspector_name="Testperson Altprüfer", legacy_report_id=7)
    for part, days in part_due:
        record_result(round_, part, Outcome.PASSED, actor=None, next_due=in_days(days))
    close_round(round_, actor=None)
    return round_


def test_legacy_round_is_adopted(make_part: MakePart) -> None:
    current, overdue = make_part(), make_part()
    round_ = legacy_round([(current, 100), (overdue, -20)])

    adopt_legacy_round(round_, comment="Import aus dem Altsystem")

    assert round_.state() == RoundState.LEGACY
    assert round_.state().label == "Altbestand übernommen"
    assert round_.inspector_display == "Testperson Altprüfer"
    assert (current.status(), overdue.status()) == (PartStatus.RELEASED, PartStatus.RELEASED)
    assert next_inspection_due(overdue) == in_days(-20)  # verstrichen, aber übernommen


def test_legacy_round_needs_inspector_name(db: None) -> None:
    with pytest.raises(InspectionError, match="Name"):
        start_round(actor=None, inspector_name=" ")
    with pytest.raises(IntegrityError), transaction.atomic():
        InspectionRound.objects.create(inspector_name="")


def test_legacy_report_is_adopted_only_once(make_part: MakePart) -> None:
    legacy_round([(make_part(), 100)])

    with pytest.raises(IntegrityError), transaction.atomic():
        start_round(actor=None, inspector_name="Testperson Altprüfer", legacy_report_id=7)


def test_legacy_round_cannot_be_signed_or_used_by_accounts(
    make_part: MakePart, inspector: User
) -> None:
    round_ = legacy_round([(make_part(), 100)])

    with pytest.raises(InspectionError, match="Altsystem"):
        sign_round(round_, actor=inspector)
    with pytest.raises(InspectionError, match="Altsystem"):
        record_result(round_, make_part(), Outcome.PASSED, actor=inspector, next_due=in_days(9))


def test_regular_round_cannot_be_adopted_without_signature(
    make_part: MakePart, inspector: User
) -> None:
    round_ = start_round(actor=inspector)
    record_result(round_, make_part(), Outcome.PASSED, actor=inspector, next_due=in_days(365))
    close_round(round_, actor=inspector)

    with pytest.raises(InspectionError, match="Altsystem"):
        adopt_legacy_round(round_)
    with pytest.raises(InspectionError, match="handelt nur"):
        record_result(round_, make_part(), Outcome.PASSED, actor=None, next_due=in_days(9))


def test_accounts_cannot_create_legacy_rounds(inspector: User) -> None:
    with pytest.raises(InspectionError, match="übernommen"):
        start_round(actor=inspector, legacy_report_id=8)
