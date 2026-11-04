"""Hinweis vor dem Ablauf der Sachkunde (#26, Festlegung Felix 04.11.2026).

60 Tage vorher auf der Startseite für die Person und für den Admin; abgelaufen bleibt der
Hinweis bei Rolle ab PSA-Sachkundig; eine lückenlose Verlängerung schiebt das Ende hinaus;
widerrufene Einträge zählen nicht.
"""

from datetime import date, timedelta

import pytest
from django.core.exceptions import PermissionDenied
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from accounts import expiry
from accounts.models import Qualification, Role, User
from accounts.services import revoke_qualification
from tests.helpers import make_inspector

pytestmark = pytest.mark.django_db
DAY = timedelta(days=1)


def today() -> date:
    return timezone.localdate()


def qualified(user: User, start: date, end: date) -> Qualification:
    return Qualification.objects.create(
        user=user, valid_from=start, valid_until=end, evidence="Lehrgang (Testdaten)"
    )


def expert(**fields: object) -> User:
    return make_inspector(qualified=False, **fields)  # type: ignore[arg-type]


@pytest.mark.parametrize(("days", "shown"), [(60, True), (61, False), (0, True)])
def test_notice_from_sixty_days_before(days: int, shown: bool) -> None:
    user = expert()
    qualified(user, today() - 300 * DAY, today() + days * DAY)

    notice = expiry.expiry(user)

    assert (notice is not None) is shown
    if notice is not None:
        assert (notice.until, notice.days_left, notice.expired) == (
            today() + days * DAY,
            days,
            False,
        )


def test_seamless_renewal_moves_the_end() -> None:
    user = expert()
    qualified(user, today() - 300 * DAY, today() + 10 * DAY)
    qualified(user, today() + 11 * DAY, today() + 400 * DAY)

    assert expiry.qualified_until(user) == today() + 400 * DAY
    assert expiry.expiry(user) is None


def test_gap_after_the_current_entry_counts_as_end() -> None:
    user = expert()
    qualified(user, today() - 300 * DAY, today() + 10 * DAY)
    qualified(user, today() + 20 * DAY, today() + 400 * DAY)

    assert expiry.qualified_until(user) == today() + 10 * DAY


def test_revoked_entry_does_not_count() -> None:
    user = expert()
    renewal = qualified(user, today() - 10 * DAY, today() + 400 * DAY)
    qualified(user, today() - 300 * DAY, today() + 30 * DAY)
    revoke_qualification(renewal, actor=None, reason="Nachweis zurückgezogen (Testdaten)")

    notice = expiry.expiry(user)

    assert notice is not None and notice.until == today() + 30 * DAY


def test_expired_qualification_keeps_the_notice() -> None:
    user = expert()
    qualified(user, today() - 400 * DAY, today() - 5 * DAY)

    notice = expiry.expiry(user)

    assert notice is not None and notice.expired and notice.until == today() - 5 * DAY


def test_lender_without_qualification_gets_no_notice() -> None:
    lender = expert(role=Role.LENDER)
    trainer_with_old = expert(role=Role.TRAINER)
    qualified(trainer_with_old, today() - 400 * DAY, today() - 5 * DAY)

    assert expiry.expiry(lender) is None
    assert expiry.expiry(trainer_with_old) is None


def test_admin_overview_lists_others_expired_first() -> None:
    admin = make_inspector(role=Role.ADMIN)
    soon = expert()
    qualified(soon, today() - 300 * DAY, today() + 20 * DAY)
    gone = expert()
    qualified(gone, today() - 400 * DAY, today() - 2 * DAY)
    fine = expert()
    qualified(fine, today() - 10 * DAY, today() + 500 * DAY)
    inactive = expert(is_active=False)
    qualified(inactive, today() - 300 * DAY, today() + 5 * DAY)

    listed = [notice.user for notice in expiry.overview(admin)]

    assert listed == [gone, soon]


@pytest.mark.parametrize("role", [Role.PPE_EXPERT, Role.TRAINER, Role.LENDER, Role.VIEWER])
def test_overview_only_for_admin(role: Role) -> None:
    with pytest.raises(PermissionDenied):
        expiry.overview(make_inspector(role=role))


def test_start_page_shows_own_and_admin_notices(client: Client) -> None:
    user = expert()
    qualified(user, today() - 300 * DAY, today() + 20 * DAY)
    admin = make_inspector(role=Role.ADMIN)

    client.force_login(user)
    own = client.get(reverse("home")).content.decode()
    client.force_login(admin)
    admins = client.get(reverse("home")).content.decode()

    assert "Deine Sachkunde gilt noch bis" in own and "Sachkunde läuft ab" not in own
    assert "Sachkunde läuft ab" in admins and user.get_full_name() in admins
    assert reverse("accounts:user_detail", args=[user.pk]) in admins
