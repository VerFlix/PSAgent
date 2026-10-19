"""Prüfmodus (Parität 7): Teile sammeln, Ergebnisse erfassen, abschließen.

Festlegungen (Felix, 19.10.2026):

- Je Sachkundigem höchstens eine offene Runde; der Prüfmodus setzt sie fort.
- Gescannte Teile kommen zuerst in die Liste der Runde; Ergebnisse folgen je Teil oder für
  alle übrigen auf einmal („in Ordnung“, nächste Prüfung).
- Abgeschlossene Runden wirken erst mit der Signatur (#38).
"""

import pytest
from django.core.exceptions import PermissionDenied

from accounts.models import Role, User
from inspections import services
from inspections.models import Outcome, RoundPart, RoundState
from inspections.services import InspectionError
from inventory.models import EventKind
from inventory.services import record_event
from tests.helpers import make_inspector
from tests.inspections.conftest import MakePart, in_days

pytestmark = pytest.mark.django_db


def ei(part: object) -> str:
    return str(part.components.get().ei)  # type: ignore[attr-defined]


# --- Eine offene Runde ---


def test_parity_7_one_open_round_per_inspector(inspector: User) -> None:
    first = services.start_round(actor=inspector)

    with pytest.raises(InspectionError, match="bereits eine offene Prüfungsrunde"):
        services.start_round(actor=inspector)

    assert services.open_round(inspector) == first
    assert services.open_round(make_inspector()) is None


def test_new_round_after_close_or_cancel(inspector: User, make_part: MakePart) -> None:
    first = services.start_round(actor=inspector)
    services.cancel_round(first, actor=inspector, comment="falsch begonnen")

    second = services.start_round(actor=inspector)

    assert second != first and services.open_round(inspector) == second


# --- Sammeln ---


def test_parity_7_scanned_parts_are_collected(inspector: User, make_part: MakePart) -> None:
    round_ = services.start_round(actor=inspector)
    part = make_part()

    found = services.collect_by_ei(round_, f"  {ei(part).lower()} ", actor=inspector)

    assert found == part
    assert list(services.pending_parts(round_)) == [part]
    assert round_.state() == RoundState.OPEN


def test_collecting_twice_keeps_one_entry(inspector: User, make_part: MakePart) -> None:
    round_ = services.start_round(actor=inspector)
    part = make_part()

    services.collect(round_, part, actor=inspector)
    services.collect(round_, part, actor=inspector)

    assert RoundPart.objects.filter(round=round_, part=part).count() == 1


@pytest.mark.parametrize("case", ["unbekannt", "ausgesondert"])
def test_unknown_or_retired_ei_is_refused(case: str, inspector: User, make_part: MakePart) -> None:
    round_ = services.start_round(actor=inspector)
    part = make_part()
    if case == "ausgesondert":
        record_event(part, EventKind.RETIRED, actor=None, comment="Test")
        expected = "ausgesondert"
    else:
        expected = "unbekannt"

    with pytest.raises(InspectionError, match=expected):
        services.collect_by_ei(
            round_, "TST-GIBTSNICHT" if case == "unbekannt" else ei(part), actor=inspector
        )

    assert not RoundPart.objects.exists()


def test_collected_part_can_be_dropped(inspector: User, make_part: MakePart) -> None:
    round_ = services.start_round(actor=inspector)
    part = make_part()
    services.collect(round_, part, actor=inspector)

    services.drop(round_, part, actor=inspector)

    assert list(services.pending_parts(round_)) == []


def test_part_with_result_cannot_be_dropped(inspector: User, make_part: MakePart) -> None:
    round_ = services.start_round(actor=inspector)
    part = make_part()
    services.record_result(round_, part, Outcome.PASSED, actor=inspector, next_due=in_days(365))

    with pytest.raises(InspectionError, match="hat schon ein Ergebnis"):
        services.drop(round_, part, actor=inspector)


# --- Ergebnisse ---


def test_parity_7_result_moves_part_out_of_pending(inspector: User, make_part: MakePart) -> None:
    round_ = services.start_round(actor=inspector)
    first, second = make_part(), make_part()
    services.collect(round_, first, actor=inspector)
    services.collect(round_, second, actor=inspector)

    services.record_result(round_, first, Outcome.DEFECT, actor=inspector, comment="Naht offen")

    assert list(services.pending_parts(round_)) == [second]


def test_parity_7_all_remaining_passed(inspector: User, make_part: MakePart) -> None:
    round_ = services.start_round(actor=inspector)
    defect, *rest = make_part(), make_part(), make_part()
    for part in (defect, *rest):
        services.collect(round_, part, actor=inspector)
    services.record_result(round_, defect, Outcome.DEFECT, actor=inspector, comment="Riss")

    count = services.pass_remaining(round_, actor=inspector, next_due=in_days(365))

    assert count == 2
    results = {r.part: r for r in round_.current_results()}
    assert results[defect].outcome == Outcome.DEFECT
    assert all(results[p].outcome == Outcome.PASSED for p in rest)
    assert all(results[p].next_due == in_days(365) for p in rest)


def test_result_in_open_round_is_changed_by_correction(
    inspector: User, make_part: MakePart
) -> None:
    round_ = services.start_round(actor=inspector)
    part = make_part()
    services.record_result(round_, part, Outcome.PASSED, actor=inspector, next_due=in_days(365))

    services.set_result(round_, part, Outcome.DEFECT, actor=inspector, comment="doch Mangel")

    [current] = round_.current_results()
    assert current.outcome == Outcome.DEFECT and current.corrects is not None


def test_close_needs_results_for_all_collected(inspector: User, make_part: MakePart) -> None:
    round_ = services.start_round(actor=inspector)
    done, open_ = make_part(), make_part()
    services.collect(round_, open_, actor=inspector)
    services.record_result(round_, done, Outcome.PASSED, actor=inspector, next_due=in_days(365))

    with pytest.raises(InspectionError, match="ohne Ergebnis"):
        services.close_round(round_, actor=inspector)

    services.drop(round_, open_, actor=inspector)
    services.close_round(round_, actor=inspector)
    assert round_.state() == RoundState.CLOSED


def test_closed_round_has_no_effect_until_signed(inspector: User, make_part: MakePart) -> None:
    """Festlegung: Ohne Signatur (#38) bleibt der Bestand unverändert."""
    round_ = services.start_round(actor=inspector)
    part = make_part()
    services.record_result(round_, part, Outcome.PASSED, actor=inspector, next_due=in_days(365))
    services.close_round(round_, actor=inspector)

    part.refresh_from_db()
    assert not part.is_released()
    assert services.next_inspection_due(part) is None


# --- Rechte ---


@pytest.mark.parametrize("role", [Role.TRAINER, Role.LENDER, Role.VIEWER])
def test_parity_7_only_ppe_experts_start_rounds(role: Role) -> None:
    """Verbotener Fall: Prüfen nur mit Rolle PSA-Sachkundig und gültiger Sachkunde."""
    with pytest.raises(PermissionDenied):
        services.start_round(actor=make_inspector(role=role))


def test_expert_without_qualification_cannot_collect(make_part: MakePart) -> None:
    expert = make_inspector()
    round_ = services.start_round(actor=expert)
    unqualified = make_inspector(qualified=False)

    with pytest.raises((PermissionDenied, InspectionError)):
        services.collect(round_, make_part(), actor=unqualified)


def test_other_inspector_cannot_touch_the_round(inspector: User, make_part: MakePart) -> None:
    round_ = services.start_round(actor=inspector)
    part = make_part()
    services.collect(round_, part, actor=inspector)
    other = make_inspector()

    for action in (
        lambda: services.collect(round_, make_part(), actor=other),
        lambda: services.drop(round_, part, actor=other),
        lambda: services.pass_remaining(round_, actor=other, next_due=in_days(365)),
    ):
        with pytest.raises(InspectionError, match="handelt nur"):
            action()
    assert list(services.pending_parts(round_)) == [part]
