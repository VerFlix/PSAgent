"""Oberfläche der Nutzerverwaltung und Einrichtungslink mit eigenem Passwort (#67).

Dass nur Admins die Verwaltungsseiten erreichen (403 für alle anderen Rollen, nichts ändert
sich), prüft `tests/test_url_rights.py` für jede URL.
"""

from datetime import timedelta
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from accounts.enrollment import find_token
from accounts.models import (
    AccountEvent,
    AccountEventKind,
    EnrollmentToken,
    Passkey,
    Qualification,
    Role,
    User,
)
from accounts.services import issue_enrollment
from tests.accounts.conftest import MakeUser
from tests.yubikey_client import login_with_yubikey

pytestmark = pytest.mark.django_db
PASSWORD = "ein-langes-Test-Passwort-42"  # gitleaks:allow – künstliches Testpasswort


@pytest.fixture
def admin(make_user: MakeUser, client: Client) -> User:
    user = make_user(Role.ADMIN)
    login_with_yubikey(client, user)
    return user


def token_from(response: Any) -> str:
    link: str = response.context["link"]
    return link.rstrip("/").rsplit("/", 1)[-1]


def messages_of(response: Any) -> list[str]:
    return [str(message) for message in response.context["messages"]]


# --- Konto anlegen ---


def test_admin_creates_account_and_sees_link_once(client: Client, admin: User) -> None:
    response = client.post(
        reverse("accounts:user_create"),
        {"username": "kim", "first_name": "Kim", "last_name": "Beispiel", "role": Role.LENDER},
    )

    assert response.status_code == 200
    assert "no-store" in response["Cache-Control"]
    token = token_from(response)
    assert "/einrichten/" in response.context["link"]
    entry = find_token(token)
    assert entry is not None and entry.user.username == "kim"
    assert EnrollmentToken.objects.get().token_hash not in response.content.decode()


def test_duplicate_username_is_shown(client: Client, admin: User, make_user: MakeUser) -> None:
    User.objects.create_user(username="kim", role=Role.VIEWER)

    response = client.post(
        reverse("accounts:user_create"),
        {"username": "Kim", "first_name": "Kim", "last_name": "Probe", "role": Role.VIEWER},
    )

    assert response.status_code == 200
    assert "schon vergeben" in response.content.decode()
    assert User.objects.filter(username__iexact="kim").count() == 1


def test_list_and_detail_show_accounts(client: Client, admin: User, make_user: MakeUser) -> None:
    user = make_user(Role.PPE_EXPERT, is_active=False)

    listing = client.get(reverse("accounts:users")).content.decode()
    detail = client.get(reverse("accounts:user_detail", args=[user.pk])).content.decode()

    assert user.username in listing and "deaktiviert" in listing
    assert "Kein Passkey eingerichtet" in detail
    assert "Zurzeit keine gültige Sachkunde" in detail


# --- Rolle und Status ---


def test_role_change_via_form(client: Client, admin: User, make_user: MakeUser) -> None:
    user = make_user(Role.LENDER)

    response = client.post(
        reverse("accounts:user_role", args=[user.pk]),
        {"role": Role.TRAINER, "reason": "Materialwart"},
        follow=True,
    )

    user.refresh_from_db()
    assert user.role == Role.TRAINER
    assert "Materialwart" in response.content.decode()  # steht im Protokoll


def test_role_change_without_reason_changes_nothing(
    client: Client, admin: User, make_user: MakeUser
) -> None:
    user = make_user(Role.LENDER)

    response = client.post(
        reverse("accounts:user_role", args=[user.pk]), {"role": Role.ADMIN}, follow=True
    )

    user.refresh_from_db()
    assert user.role == Role.LENDER
    assert any("erforderlich" in text for text in messages_of(response))
    assert not AccountEvent.objects.exists()


def test_self_demotion_via_form_is_refused(
    client: Client, admin: User, make_user: MakeUser
) -> None:
    make_user(Role.ADMIN)

    response = client.post(
        reverse("accounts:user_role", args=[admin.pk]),
        {"role": Role.VIEWER, "reason": "Test"},
        follow=True,
    )

    admin.refresh_from_db()
    assert admin.role == Role.ADMIN
    assert any("eigene Admin-Rolle" in text for text in messages_of(response))


def test_deactivation_ends_running_session(
    client: Client, admin: User, make_user: MakeUser
) -> None:
    user = make_user(Role.LENDER)
    other = Client()
    other.force_login(user)
    assert other.get(reverse("home")).status_code == 200

    client.post(reverse("accounts:user_deactivate", args=[user.pk]), {"reason": "Austritt"})

    assert other.get(reverse("home")).status_code == 302
    client.post(reverse("accounts:user_reactivate", args=[user.pk]), {"reason": "zurück"})
    user.refresh_from_db()
    assert user.is_active


def test_new_link_via_form_replaces_old(client: Client, admin: User, make_user: MakeUser) -> None:
    user = make_user(Role.LENDER)
    old = issue_enrollment(user, actor=admin)

    response = client.post(reverse("accounts:user_enrollment", args=[user.pk]))

    assert find_token(old) is None
    assert find_token(token_from(response)) is not None
    assert "no-store" in response["Cache-Control"]


# --- Sachkunde und Passkeys ---


def test_qualification_grant_and_revoke_via_form(
    client: Client, admin: User, make_user: MakeUser
) -> None:
    expert = make_user(Role.PPE_EXPERT)
    today = timezone.localdate()

    client.post(
        reverse("accounts:qualification_grant", args=[expert.pk]),
        {
            "valid_from": today.isoformat(),
            "valid_until": (today + timedelta(days=365)).isoformat(),
            "evidence": "Lehrgang PSA (Testdaten)",
        },
    )
    assert expert.may_inspect()
    entry = Qualification.objects.get()
    client.post(
        reverse("accounts:qualification_revoke", args=[expert.pk, entry.pk]),
        {"reason": "zurückgezogen"},
    )

    assert not expert.may_inspect()


def test_qualification_with_wrong_dates_is_refused(
    client: Client, admin: User, make_user: MakeUser
) -> None:
    expert = make_user(Role.PPE_EXPERT)
    today = timezone.localdate()

    response = client.post(
        reverse("accounts:qualification_grant", args=[expert.pk]),
        {
            "valid_from": today.isoformat(),
            "valid_until": (today - timedelta(days=1)).isoformat(),
            "evidence": "Lehrgang",
        },
        follow=True,
    )

    assert not Qualification.objects.exists()
    assert any("vor „gültig ab“" in text for text in messages_of(response))


def test_admin_revokes_passkey_of_other_account(
    client: Client, admin: User, make_user: MakeUser
) -> None:
    user = make_user(Role.PPE_EXPERT)
    passkey = Passkey.objects.create(
        user=user, name="YubiKey", credential_id=b"k1", public_key=b"\x00", sign_count=0
    )

    client.post(
        reverse("accounts:user_passkey_revoke", args=[user.pk, passkey.pk]),
        {"reason": "verloren"},
    )

    passkey.refresh_from_db()
    assert passkey.revoked_at is not None
    assert AccountEvent.objects.get().kind == AccountEventKind.PASSKEY_REVOKED


def test_passkey_must_belong_to_the_account_in_the_url(
    client: Client, admin: User, make_user: MakeUser
) -> None:
    owner = make_user(Role.LENDER)
    other = make_user(Role.LENDER)
    passkey = Passkey.objects.create(
        user=owner, name="YubiKey", credential_id=b"k2", public_key=b"\x00", sign_count=0
    )

    response = client.post(
        reverse("accounts:user_passkey_revoke", args=[other.pk, passkey.pk]), {"reason": "x"}
    )

    assert response.status_code == 404
    passkey.refresh_from_db()
    assert passkey.revoked_at is None


def test_home_links_management_only_for_admin(client: Client, make_user: MakeUser) -> None:
    client.force_login(make_user(Role.PPE_EXPERT))
    assert "Nutzer verwalten" not in client.get(reverse("home")).content.decode()

    client.force_login(make_user(Role.ADMIN))
    assert "Nutzer verwalten" in client.get(reverse("home")).content.decode()


# --- Einrichtungslink mit eigenem Passwort ---


def set_password(client: Client, token: str, password: str = PASSWORD) -> Any:
    return client.post(
        reverse("accounts:enroll_password", args=[token]),
        {"new_password1": password, "new_password2": password},
    )


@pytest.mark.parametrize("role", [Role.TRAINER, Role.LENDER, Role.VIEWER])
def test_password_via_link_for_roles_without_passkey_duty(
    role: Role, client: Client, make_user: MakeUser
) -> None:
    user = make_user(role)
    token = issue_enrollment(user, actor=None)
    assert (
        "Passwort festlegen"
        in client.get(reverse("accounts:enroll", args=[token])).content.decode()
    )

    response = set_password(client, token)

    assert response.status_code == 302
    assert client.session["_auth_user_id"] == str(user.pk)
    assert find_token(token) is None
    client.logout()
    login = client.post(
        reverse("accounts:login"), {"username": user.username, "password": PASSWORD}
    )
    assert login.status_code == 302


@pytest.mark.parametrize("role", [Role.ADMIN, Role.PPE_EXPERT])
def test_no_password_via_link_for_passkey_roles(
    role: Role, client: Client, make_user: MakeUser
) -> None:
    """Verbotener Fall: Admin und PSA-Sachkundige bekommen auch über den Link kein Passwort."""
    user = make_user(role)
    token = issue_enrollment(user, actor=None)
    assert (
        "Passwort festlegen"
        not in client.get(reverse("accounts:enroll", args=[token])).content.decode()
    )

    response = set_password(client, token)

    assert response.status_code == 403
    user.refresh_from_db()
    assert not user.has_usable_password()
    assert find_token(token) is not None
    assert "_auth_user_id" not in client.session


@pytest.mark.parametrize(
    ("first", "second"),
    [("kurz", "kurz"), ("123456789012345", "123456789012345"), (PASSWORD, PASSWORD + "x")],
)
def test_weak_or_mismatched_password_keeps_link_unused(
    first: str, second: str, client: Client, make_user: MakeUser
) -> None:
    user = make_user(Role.LENDER)
    token = issue_enrollment(user, actor=None)

    response = client.post(
        reverse("accounts:enroll_password", args=[token]),
        {"new_password1": first, "new_password2": second},
    )

    assert response.status_code == 400
    user.refresh_from_db()
    assert not user.has_usable_password()
    assert find_token(token) is not None


def test_password_link_works_once(client: Client, make_user: MakeUser) -> None:
    user = make_user(Role.LENDER)
    token = issue_enrollment(user, actor=None)
    set_password(client, token)
    client.logout()

    response = set_password(client, token, PASSWORD + "-neu")

    assert response.status_code == 404
    assert "_auth_user_id" not in client.session


# --- Laufende Sitzungen nach Rollenwechsel und Passkey-Widerruf ---


def session_of(user: User) -> Client:
    other = Client()
    other.force_login(user)
    assert other.get(reverse("home")).status_code == 200
    return other


@pytest.mark.parametrize("role", [Role.PPE_EXPERT, Role.ADMIN, Role.VIEWER])
def test_role_change_ends_running_sessions(
    role: Role, client: Client, admin: User, make_user: MakeUser
) -> None:
    """Sonst behielte eine Passwort-Sitzung nach der Beförderung die neuen Rechte."""
    user = make_user(Role.LENDER)
    running = session_of(user)

    client.post(reverse("accounts:user_role", args=[user.pk]), {"role": role, "reason": "Wechsel"})

    assert running.get(reverse("home")).status_code == 302
    assert client.get(reverse("accounts:users")).status_code == 200  # Admin bleibt angemeldet


def test_admin_passkey_revocation_ends_running_sessions(
    client: Client, admin: User, make_user: MakeUser
) -> None:
    """Ein gestohlener Schlüssel: auch die damit geöffnete Sitzung endet sofort."""
    user = make_user(Role.PPE_EXPERT)
    passkey = Passkey.objects.create(
        user=user, name="YubiKey", credential_id=b"k3", public_key=b"\x00", sign_count=0
    )
    running = session_of(user)

    client.post(
        reverse("accounts:user_passkey_revoke", args=[user.pk, passkey.pk]),
        {"reason": "gestohlen"},
    )

    assert running.get(reverse("home")).status_code == 302


def test_own_passkey_revocation_ends_other_sessions_only(
    client: Client, make_user: MakeUser
) -> None:
    user = make_user(Role.LENDER)
    passkey = Passkey.objects.create(
        user=user, name="YubiKey", credential_id=b"k4", public_key=b"\x00", sign_count=0
    )
    elsewhere = session_of(user)
    client.force_login(user)

    client.post(reverse("accounts:passkey_revoke", args=[passkey.pk]))

    assert elsewhere.get(reverse("home")).status_code == 302
    assert client.get(reverse("home")).status_code == 200


def test_unrelated_changes_keep_sessions(client: Client, admin: User, make_user: MakeUser) -> None:
    user = make_user(Role.PPE_EXPERT)
    running = session_of(user)
    today = timezone.localdate()

    client.post(
        reverse("accounts:qualification_grant", args=[user.pk]),
        {"valid_from": today.isoformat(), "valid_until": today.isoformat(), "evidence": "Lehrgang"},
    )

    assert running.get(reverse("home")).status_code == 200
