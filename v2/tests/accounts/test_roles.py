"""Rollen bauen aufeinander auf.

Rangfolge: Admin > PSA-Sachkundig > Trainer/Materialwart > Verleiher > Viewer.
"""

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import IntegrityError, transaction

from accounts.models import ROLE_ORDER, Role, User
from tests.accounts.conftest import MakeUser

ORDER = [Role.VIEWER, Role.LENDER, Role.TRAINER, Role.PPE_EXPERT, Role.ADMIN]


def test_role_order_matches_concept() -> None:
    assert list(ROLE_ORDER) == ORDER
    assert [role.label for role in reversed(ORDER)] == [
        "Admin",
        "PSA-Sachkundig",
        "Trainer/Materialwart",
        "Verleiher",
        "Viewer",
    ]


@pytest.mark.parametrize("held", ORDER)
@pytest.mark.parametrize("required", ORDER)
def test_role_includes_exactly_the_roles_below(
    held: Role, required: Role, make_user: MakeUser
) -> None:
    """Jede Rolle hat die Rechte der darunterliegenden, nie die einer höheren."""
    user = make_user(role=held)

    assert user.has_role(required) is (ORDER.index(held) >= ORDER.index(required))


@pytest.mark.parametrize("required", ORDER)
def test_deactivated_account_has_no_role(required: Role, make_user: MakeUser) -> None:
    user = make_user(role=Role.ADMIN, is_active=False)

    assert user.has_role(required) is False


def test_new_account_is_viewer(make_user: MakeUser) -> None:
    user = User.objects.create_user(username="neu")

    assert user.role == Role.VIEWER


def test_database_rejects_unknown_role(make_user: MakeUser) -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        make_user(role="chef")


def test_custom_user_model_is_active() -> None:
    assert get_user_model() is User


@pytest.mark.django_db
def test_superuser_is_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DJANGO_SUPERUSER_PASSWORD", "nur-fuer-tests-1234")

    call_command("createsuperuser", interactive=False, username="admin", email="admin@example.org")

    assert User.objects.get(username="admin").role == Role.ADMIN


def test_stamp_defaults_to_full_name(make_user: MakeUser) -> None:
    user = make_user(role=Role.PPE_EXPERT)

    assert user.stamp == user.get_full_name()


def test_stamp_uses_stamp_text_when_set(make_user: MakeUser) -> None:
    user = make_user(role=Role.PPE_EXPERT, stamp_text="Testperson Beispiel\nBeispielverein")

    assert user.stamp == "Testperson Beispiel\nBeispielverein"
