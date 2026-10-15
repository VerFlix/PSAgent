"""Sachkunde: Prüfen und Signieren nur mit Rolle ab PSA-Sachkundig und gültiger Sachkunde.

Verbotene Fälle stehen gleichberechtigt neben den erlaubten (CLAUDE.md, Regel 5).
"""

from datetime import datetime, timedelta

import pytest
from django.db import IntegrityError, transaction

from accounts.models import Qualification, QualificationRevocation, Role
from tests.accounts.conftest import BERLIN, NOW, TODAY, Grant, MakeUser

DAY = timedelta(days=1)


@pytest.mark.parametrize("role", [Role.PPE_EXPERT, Role.ADMIN])
def test_valid_qualification_allows_inspection(
    role: Role, make_user: MakeUser, grant: Grant
) -> None:
    user = make_user(role=role)
    grant(user, TODAY - 30 * DAY, TODAY + 300 * DAY)

    assert user.has_valid_qualification(NOW) is True
    assert user.may_inspect(NOW) is True


@pytest.mark.parametrize("role", [Role.PPE_EXPERT, Role.ADMIN])
def test_role_without_qualification_may_not_inspect(role: Role, make_user: MakeUser) -> None:
    """Auch ein Admin ohne Sachkunde darf nicht prüfen und nicht signieren."""
    user = make_user(role=role)

    assert user.may_inspect(NOW) is False


@pytest.mark.parametrize("role", [Role.TRAINER, Role.LENDER, Role.VIEWER])
def test_qualification_without_role_may_not_inspect(
    role: Role, make_user: MakeUser, grant: Grant
) -> None:
    """Die Sachkunde allein reicht nicht; die Rolle muss mindestens PSA-Sachkundig sein."""
    user = make_user(role=role)
    grant(user, TODAY - 30 * DAY, TODAY + 300 * DAY)

    assert user.has_valid_qualification(NOW) is True
    assert user.may_inspect(NOW) is False


def test_expired_qualification_no_longer_counts(make_user: MakeUser, grant: Grant) -> None:
    user = make_user(role=Role.PPE_EXPERT)
    grant(user, TODAY - 400 * DAY, TODAY - DAY)

    assert user.may_inspect(NOW) is False


def test_qualification_is_valid_through_its_last_day(make_user: MakeUser, grant: Grant) -> None:
    user = make_user(role=Role.PPE_EXPERT)
    grant(user, TODAY - 400 * DAY, TODAY)

    assert user.may_inspect(NOW) is True


def test_qualification_not_yet_valid_does_not_count(make_user: MakeUser, grant: Grant) -> None:
    user = make_user(role=Role.PPE_EXPERT)
    grant(user, TODAY + DAY, TODAY + 400 * DAY)

    assert user.may_inspect(NOW) is False


def test_validity_follows_the_calendar_day_in_berlin(make_user: MakeUser, grant: Grant) -> None:
    """Gültig bis einschließlich des letzten Tages nach deutscher Zeit, nicht nach UTC."""
    user = make_user(role=Role.PPE_EXPERT)
    grant(user, TODAY - 400 * DAY, TODAY)

    late_evening = datetime(TODAY.year, TODAY.month, TODAY.day, 23, 30, tzinfo=BERLIN)
    after_midnight = late_evening + timedelta(hours=1)

    assert user.may_inspect(late_evening) is True
    assert user.may_inspect(after_midnight) is False


def test_revocation_counts_from_its_moment_on(make_user: MakeUser, grant: Grant) -> None:
    """Vor dem Widerruf war die Sachkunde gültig; Signaturen aus dieser Zeit bleiben gültig."""
    user = make_user(role=Role.PPE_EXPERT)
    qualification = grant(user, TODAY - 30 * DAY, TODAY + 300 * DAY)
    revoked_at = NOW - timedelta(hours=2)
    QualificationRevocation.objects.create(
        qualification=qualification, revoked_at=revoked_at, reason="Lehrgang nicht anerkannt"
    )

    assert user.may_inspect(NOW) is False
    assert user.may_inspect(revoked_at - timedelta(minutes=1)) is True


def test_renewal_is_a_new_entry(make_user: MakeUser, grant: Grant) -> None:
    user = make_user(role=Role.PPE_EXPERT)
    grant(user, TODAY - 400 * DAY, TODAY - DAY)
    grant(user, TODAY, TODAY + 365 * DAY)

    assert user.may_inspect(NOW) is True
    assert user.qualifications.count() == 2


def test_deactivated_account_may_not_inspect(make_user: MakeUser, grant: Grant) -> None:
    user = make_user(role=Role.PPE_EXPERT, is_active=False)
    grant(user, TODAY - 30 * DAY, TODAY + 300 * DAY)

    assert user.may_inspect(NOW) is False


def test_end_before_start_is_rejected(make_user: MakeUser, grant: Grant) -> None:
    user = make_user(role=Role.PPE_EXPERT)

    with pytest.raises(IntegrityError), transaction.atomic():
        grant(user, TODAY, TODAY - DAY)


@pytest.mark.parametrize("reason", ["", "   "])
def test_revocation_requires_a_reason(reason: str, make_user: MakeUser, grant: Grant) -> None:
    qualification = grant(make_user(role=Role.PPE_EXPERT), TODAY - DAY, TODAY + DAY)

    with pytest.raises(IntegrityError), transaction.atomic():
        QualificationRevocation.objects.create(qualification=qualification, reason=reason)


def test_qualification_can_be_revoked_only_once(make_user: MakeUser, grant: Grant) -> None:
    qualification = grant(make_user(role=Role.PPE_EXPERT), TODAY - DAY, TODAY + DAY)
    QualificationRevocation.objects.create(qualification=qualification, reason="erster Widerruf")

    with pytest.raises(IntegrityError), transaction.atomic():
        QualificationRevocation.objects.create(
            qualification=qualification, reason="zweiter Widerruf"
        )


def test_qualifications_are_listed_oldest_first(make_user: MakeUser, grant: Grant) -> None:
    user = make_user(role=Role.PPE_EXPERT)
    newer = grant(user, TODAY, TODAY + DAY)
    older = grant(user, TODAY - 400 * DAY, TODAY - DAY)

    assert list(Qualification.objects.filter(user=user)) == [older, newer]
