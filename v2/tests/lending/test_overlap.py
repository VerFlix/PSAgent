"""Überschneidende Ausleihen desselben Teils schließt die Datenbank aus (Parität 13).

Wie im Altsystem zählen Von- und Bis-Tag mit: Was am 10. zurückkommt, geht frühestens am 11.
wieder raus.
"""

from typing import Any

import pytest
from django.db import IntegrityError, connection, transaction
from psycopg.types.range import Range

from accounts.models import User
from lending.models import Booking, Borrower, Loan, LoanStatus
from lending.services import LendingError, cancel, reserve
from tests.lending.conftest import DAY, TODAY, MakePart


def raw_loan(
    booking: Booking, part_id: int, start_offset: int, end_offset: int, status: str
) -> None:
    """Schreibt eine Ausleihe direkt per SQL, an der Anwendung vorbei."""
    period = Range(TODAY + start_offset * DAY, TODAY + (end_offset + 1) * DAY)
    params: list[Any] = [booking.pk, part_id, period, status]
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO lending_loan (booking_id, part_id, period, status, created_at) "
            "VALUES (%s, %s, %s, %s, now())",
            params,
        )


@pytest.mark.parametrize(
    ("start", "end"),
    [(3, 7), (0, 3), (7, 9), (5, 5), (1, 20), (4, 6)],
)
def test_overlap_is_rejected_by_database(
    start: int, end: int, make_part: MakePart, borrower: Borrower, lender: User
) -> None:
    part = make_part()
    booking = reserve(borrower, [part], TODAY + 3 * DAY, TODAY + 7 * DAY, actor=lender)

    with pytest.raises(IntegrityError), transaction.atomic():
        raw_loan(booking, part.pk, start, end, LoanStatus.RESERVED)


def test_lent_and_reserved_block_each_other(make_part: MakePart, borrower: Borrower) -> None:
    part = make_part()
    booking = reserve(borrower, [part], TODAY + 3 * DAY, TODAY + 7 * DAY, actor=None)

    with pytest.raises(IntegrityError), transaction.atomic():
        raw_loan(booking, part.pk, 5, 6, LoanStatus.LENT)


def test_adjacent_periods_are_allowed(make_part: MakePart, borrower: Borrower) -> None:
    part = make_part()
    reserve(borrower, [part], TODAY + 3 * DAY, TODAY + 7 * DAY, actor=None)

    reserve(borrower, [part], TODAY + 8 * DAY, TODAY + 9 * DAY, actor=None)
    reserve(borrower, [part], TODAY, TODAY + 2 * DAY, actor=None)

    assert Loan.objects.filter(part=part).count() == 3


def test_same_day_handover_counts_as_overlap(make_part: MakePart, borrower: Borrower) -> None:
    part = make_part()
    reserve(borrower, [part], TODAY + 3 * DAY, TODAY + 7 * DAY, actor=None)

    with pytest.raises(LendingError, match="bereits vergeben"):
        reserve(borrower, [part], TODAY + 7 * DAY, TODAY + 9 * DAY, actor=None)


def test_other_parts_are_independent(make_part: MakePart, borrower: Borrower) -> None:
    first, second = make_part(), make_part()
    reserve(borrower, [first], TODAY, TODAY + 5 * DAY, actor=None)

    reserve(borrower, [second], TODAY, TODAY + 5 * DAY, actor=None)


def test_cancelled_reservation_frees_the_period(make_part: MakePart, borrower: Borrower) -> None:
    part = make_part()
    booking = reserve(borrower, [part], TODAY + 3 * DAY, TODAY + 7 * DAY, actor=None)
    cancel(list(booking.loans.all()), actor=None, comment="Tour abgesagt")

    reserve(borrower, [part], TODAY + 3 * DAY, TODAY + 7 * DAY, actor=None)


def test_failed_booking_with_several_parts_leaves_nothing(
    make_part: MakePart, borrower: Borrower
) -> None:
    free, taken = make_part(), make_part()
    reserve(borrower, [taken], TODAY, TODAY + 5 * DAY, actor=None)

    with pytest.raises(LendingError):
        reserve(borrower, [free, taken], TODAY + 2 * DAY, TODAY + 3 * DAY, actor=None)

    assert not Loan.objects.filter(part=free).exists()


def test_end_before_start_is_rejected(make_part: MakePart, borrower: Borrower) -> None:
    with pytest.raises(LendingError, match="Zeitraum"):
        reserve(borrower, [make_part()], TODAY + 5 * DAY, TODAY + 4 * DAY, actor=None)


def test_same_part_twice_in_one_booking_is_rejected(
    make_part: MakePart, borrower: Borrower
) -> None:
    part = make_part()

    with pytest.raises(LendingError, match="mehrfach"):
        reserve(borrower, [part, part], TODAY, TODAY + DAY, actor=None)
