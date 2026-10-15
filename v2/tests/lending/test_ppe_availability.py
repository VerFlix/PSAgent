"""Verfügbarkeit hängt an der PSA-Prüfung (Parität 13, CLAUDE.md, Fachregeln).

Verleihbar nur, wenn die Prüfung heute noch gilt (oder „keine PSA“ gesetzt ist) und die
Rückgabe spätestens am Tag der nächsten Prüfung liegt, wie im Altsystem.
"""

import pytest

from accounts.models import User
from inspections.models import Outcome
from inventory.models import EventKind
from inventory.services import record_event
from lending.models import Borrower
from lending.services import LendingError, check_out, lend, reserve, unavailability_reasons
from tests.helpers import adopt_inspection, signed_inspection
from tests.lending.conftest import DAY, TODAY, MakePart, checkout_kwargs


def test_inspection_due_today_still_counts(make_part: MakePart, borrower: Borrower) -> None:
    part = make_part(next_due=TODAY)

    reserve(borrower, [part], TODAY, TODAY, actor=None)


def test_overdue_inspection_blocks(make_part: MakePart, borrower: Borrower) -> None:
    part = make_part(next_due=TODAY - DAY)

    with pytest.raises(LendingError, match="zur PSA-Prüfung fällig"):
        reserve(borrower, [part], TODAY, TODAY + DAY, actor=None)


def test_return_must_not_be_after_next_inspection(make_part: MakePart, borrower: Borrower) -> None:
    part = make_part(next_due=TODAY + 5 * DAY)

    reserve(borrower, [part], TODAY, TODAY + 5 * DAY, actor=None)
    with pytest.raises(LendingError, match="vor der nächsten PSA-Prüfung"):
        reserve(borrower, [part], TODAY + 6 * DAY, TODAY + 6 * DAY, actor=None)
    assert unavailability_reasons(part, TODAY + 6 * DAY, TODAY + 9 * DAY) == [
        f"muss vor der nächsten PSA-Prüfung am {TODAY + 5 * DAY:%d.%m.%Y} zurück sein"
    ]


def test_no_ppe_needs_no_inspection(
    make_part: MakePart, borrower: Borrower, lender: User, inspector: User
) -> None:
    part = make_part(released=False)
    record_event(part, EventKind.NO_PPE_SET, actor=inspector)

    lend(borrower, [part], TODAY, TODAY + 400 * DAY, **checkout_kwargs(lender))


def test_released_without_inspection_is_not_available(
    make_part: MakePart, borrower: Borrower
) -> None:
    """Etwa ein Import, bei dem das Altsystem keine nächste Prüfung kannte."""
    part = make_part(released=False)
    record_event(part, EventKind.RELEASED, actor=None)

    with pytest.raises(LendingError, match="keine gültige PSA-Prüfung"):
        reserve(borrower, [part], TODAY, TODAY + DAY, actor=None)


def test_no_ppe_cleared_needs_inspection_again(
    make_part: MakePart, borrower: Borrower, inspector: User
) -> None:
    part = make_part(released=False)
    record_event(part, EventKind.NO_PPE_SET, actor=inspector)
    record_event(part, EventKind.NO_PPE_CLEARED, actor=inspector)

    with pytest.raises(LendingError, match="keine gültige PSA-Prüfung"):
        reserve(borrower, [part], TODAY, TODAY + DAY, actor=None)


def test_expired_after_reservation_blocks_checkout(
    make_part: MakePart, borrower: Borrower, lender: User
) -> None:
    part = make_part(next_due=TODAY + 10 * DAY)
    booking = reserve(borrower, [part], TODAY + 2 * DAY, TODAY + 4 * DAY, actor=lender)
    adopt_inspection([part], next_due=TODAY + 3 * DAY)  # Korrekturlauf verkürzt die Frist

    with pytest.raises(LendingError, match="vor der nächsten PSA-Prüfung"):
        check_out(list(booking.loans.all()), **checkout_kwargs(lender))


def test_defect_blocks_until_passed_again(
    make_part: MakePart, borrower: Borrower, lender: User, inspector: User
) -> None:
    part = make_part()
    signed_inspection(
        inspector, [part], outcome=Outcome.DEFECT, comment="Naht beschädigt", next_due=None
    )

    with pytest.raises(LendingError, match="gesperrt"):
        reserve(borrower, [part], TODAY, TODAY + DAY, actor=lender)

    signed_inspection(inspector, [part], comment="repariert vom Hersteller")
    reserve(borrower, [part], TODAY, TODAY + DAY, actor=lender)
