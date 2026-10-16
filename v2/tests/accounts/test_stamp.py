"""Briefkopf/Stempel pflegen (Parität 5): Admin in der Nutzerverwaltung, jede Person selbst.

Der Stempel steht auf PD und Haftungsausschluss. Jede Änderung kommt mit dem neuen Text ins
Kontoprotokoll, damit nachvollziehbar bleibt, welcher Stempel wann galt.
"""

import pytest
from django.core.exceptions import PermissionDenied
from django.test import Client
from django.urls import reverse

from accounts import services
from accounts.models import AccountEvent, AccountEventKind, Role, User
from accounts.services import AccountError
from tests.accounts.conftest import MakeUser

pytestmark = pytest.mark.django_db
STAMP = "Testperson Beispiel\nPSA-Sachkunde\nBeispielverein"


def stamp_events(user: User) -> list[AccountEvent]:
    return list(user.account_events.filter(kind=AccountEventKind.STAMP_CHANGED))


# --- Dienst ---


def test_parity_5_own_stamp_is_saved_and_logged(make_user: MakeUser) -> None:
    user = make_user(Role.PPE_EXPERT)

    services.set_stamp(user, f"  {STAMP}  \r\n", actor=user)

    user.refresh_from_db()
    assert user.stamp_text == STAMP
    [event] = stamp_events(user)
    assert event.detail == STAMP and event.actor == user


def test_parity_5_admin_sets_stamp_of_other_account(make_user: MakeUser) -> None:
    admin = make_user(Role.ADMIN)
    user = make_user(Role.PPE_EXPERT)

    services.set_stamp(user, STAMP, actor=admin)

    user.refresh_from_db()
    assert user.stamp == STAMP
    assert stamp_events(user)[0].actor == admin


def test_empty_stamp_falls_back_to_full_name(make_user: MakeUser) -> None:
    user = make_user(Role.PPE_EXPERT, stamp_text=STAMP)

    services.set_stamp(user, "   ", actor=user)

    user.refresh_from_db()
    assert user.stamp_text == "" and user.stamp == user.get_full_name()
    assert stamp_events(user)[0].detail == ""


def test_unchanged_stamp_is_not_logged(make_user: MakeUser) -> None:
    user = make_user(Role.PPE_EXPERT, stamp_text=STAMP)

    services.set_stamp(user, STAMP, actor=user)

    assert stamp_events(user) == []


@pytest.mark.parametrize("text", ["x" * 301, "\n".join(["Zeile"] * 6)])
def test_too_long_stamp_is_refused(text: str, make_user: MakeUser) -> None:
    user = make_user(Role.PPE_EXPERT)

    with pytest.raises(AccountError):
        services.set_stamp(user, text, actor=user)

    user.refresh_from_db()
    assert user.stamp_text == "" and stamp_events(user) == []


@pytest.mark.parametrize("role", [Role.PPE_EXPERT, Role.TRAINER, Role.LENDER, Role.VIEWER])
def test_others_stamp_only_by_admin(role: Role, make_user: MakeUser) -> None:
    """Verbotener Fall: Fremde Stempel ändert nur, wer Nutzer verwalten darf."""
    actor = make_user(role)
    user = make_user(Role.PPE_EXPERT)

    with pytest.raises(PermissionDenied):
        services.set_stamp(user, STAMP, actor=actor)

    user.refresh_from_db()
    assert user.stamp_text == "" and stamp_events(user) == []


# --- Oberfläche ---


def test_parity_5_stamp_on_own_account_page(client: Client, make_user: MakeUser) -> None:
    user = make_user(Role.PPE_EXPERT)
    client.force_login(user)

    page = client.get(reverse("accounts:account")).content.decode()
    response = client.post(reverse("accounts:stamp"), {"stamp_text": STAMP}, follow=True)

    assert 'name="stamp_text"' in page
    user.refresh_from_db()
    assert user.stamp_text == STAMP
    assert "Briefkopf/Stempel gespeichert." in response.content.decode()


def test_parity_5_stamp_in_user_management(client: Client, make_user: MakeUser) -> None:
    client.force_login(make_user(Role.ADMIN))
    user = make_user(Role.PPE_EXPERT)

    client.post(reverse("accounts:user_stamp", args=[user.pk]), {"stamp_text": STAMP})
    page = client.get(reverse("accounts:user_detail", args=[user.pk])).content.decode()

    user.refresh_from_db()
    assert user.stamp_text == STAMP
    assert "Briefkopf/Stempel geändert" in page and "PSA-Sachkunde" in page


def test_too_long_stamp_shows_message(client: Client, make_user: MakeUser) -> None:
    user = make_user(Role.PPE_EXPERT)
    client.force_login(user)

    response = client.post(reverse("accounts:stamp"), {"stamp_text": "x" * 301}, follow=True)

    assert "höchstens 300 Zeichen" in response.content.decode()
    user.refresh_from_db()
    assert user.stamp_text == ""


def test_own_stamp_form_changes_only_own_account(client: Client, make_user: MakeUser) -> None:
    """Verbotener Fall: Über „Konto“ lässt sich kein fremder Stempel setzen."""
    user = make_user(Role.LENDER)
    other = make_user(Role.PPE_EXPERT)
    client.force_login(user)

    client.post(reverse("accounts:stamp"), {"stamp_text": STAMP, "user": other.pk})

    other.refresh_from_db()
    user.refresh_from_db()
    assert other.stamp_text == "" and user.stamp_text == STAMP
