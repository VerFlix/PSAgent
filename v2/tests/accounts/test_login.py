"""Anmeldung mit Passwort, Sperre nach Fehlversuchen, Sitzungsdauer (#11)."""

from datetime import timedelta
from typing import Any

import pytest
from django.conf import settings
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role, User
from tests.accounts.conftest import MakeUser

PASSWORD = "ein-langes-Test-Passwort-42"  # gitleaks:allow – künstliches Testpasswort


def login(client: Client, username: str, password: str = PASSWORD) -> Any:
    return client.post(reverse("accounts:login"), {"username": username, "password": password})


@pytest.fixture
def lender(make_user: MakeUser) -> User:
    user = make_user(Role.LENDER)
    user.set_password(PASSWORD)
    user.save()
    return user


def test_password_login(client: Client, lender: User) -> None:
    response = login(client, lender.username)

    assert response.status_code == 302
    assert response["Location"] == reverse("home")
    assert client.get(reverse("home")).status_code == 200


@pytest.mark.parametrize("password", ["falsch", "", PASSWORD.upper()])
def test_wrong_password_is_rejected(password: str, client: Client, lender: User) -> None:
    """Verbotener Fall: falsches Passwort."""
    response = login(client, lender.username, password)

    assert response.status_code == 200
    assert "_auth_user_id" not in client.session
    assert client.get(reverse("home")).status_code == 302


def test_inactive_account_is_rejected(client: Client, lender: User) -> None:
    lender.is_active = False
    lender.save()

    login(client, lender.username)

    assert "_auth_user_id" not in client.session


@pytest.mark.parametrize("role", [Role.ADMIN, Role.PPE_EXPERT])
def test_admin_and_expert_cannot_use_password(
    role: Role, client: Client, make_user: MakeUser
) -> None:
    """Konzept, Abschnitt 5: Admin und PSA-Sachkundige nur mit Passkey."""
    user = make_user(role)
    user.set_password(PASSWORD)
    user.save()

    response = login(client, user.username)

    assert response.status_code == 200
    assert "nur mit Passkey" in response.content.decode()
    assert "_auth_user_id" not in client.session


@pytest.mark.parametrize("role", [Role.TRAINER, Role.LENDER, Role.VIEWER])
def test_other_roles_may_use_password(role: Role, client: Client, make_user: MakeUser) -> None:
    user = make_user(role)
    user.set_password(PASSWORD)
    user.save()

    login(client, user.username)

    assert client.session["_auth_user_id"] == str(user.pk)


def test_lockout_after_five_failures(client: Client, lender: User) -> None:
    """Schutz gegen Durchprobieren: nach 5 Fehlversuchen gesperrt, auch mit richtigem Passwort."""
    for _ in range(5):
        login(client, lender.username, "falsch")

    response = login(client, lender.username)

    assert response.status_code == 429
    assert "Zu viele Fehlversuche" in response.content.decode()
    assert "_auth_user_id" not in client.session


def test_lockout_is_per_username_and_ip(client: Client, lender: User, make_user: MakeUser) -> None:
    other = make_user(Role.LENDER)
    other.set_password(PASSWORD)
    other.save()
    for _ in range(5):
        login(client, lender.username, "falsch")

    login(client, other.username)

    assert client.session["_auth_user_id"] == str(other.pk)


def test_success_resets_failure_count(client: Client, lender: User) -> None:
    for _ in range(4):
        login(client, lender.username, "falsch")
    login(client, lender.username)
    client.post(reverse("accounts:logout"))
    for _ in range(4):
        login(client, lender.username, "falsch")

    login(client, lender.username)

    assert client.session["_auth_user_id"] == str(lender.pk)


def test_redirect_after_login_stays_on_site(client: Client, lender: User) -> None:
    response = client.post(
        reverse("accounts:login"),
        {"username": lender.username, "password": PASSWORD, "next": "https://example.org/"},
    )

    assert response["Location"] == reverse("home")


def test_session_lasts_eight_hours(client: Client, lender: User) -> None:
    login(client, lender.username)

    assert settings.SESSION_COOKIE_AGE == 8 * 60 * 60
    assert client.session.get_expiry_age() == 8 * 60 * 60


def test_expired_session_is_logged_out(client: Client, lender: User) -> None:
    """Verbotener Fall: abgelaufene Sitzung."""
    login(client, lender.username)
    session = client.session
    session.set_expiry(timezone.now() - timedelta(seconds=1))
    session.save()

    response = client.get(reverse("home"))

    assert response.status_code == 302
    assert response["Location"].startswith(reverse("accounts:login"))


def test_logout_needs_post(client: Client, lender: User) -> None:
    login(client, lender.username)

    assert client.get(reverse("accounts:logout")).status_code == 405
    client.post(reverse("accounts:logout"))
    assert "_auth_user_id" not in client.session


@pytest.mark.parametrize("name", ["home", "accounts:account"])
def test_pages_need_login(name: str, client: Client) -> None:
    response = client.get(reverse(name))

    assert response.status_code == 302
    assert response["Location"].startswith(reverse("accounts:login"))


def test_login_page_has_no_inline_script(client: Client) -> None:
    """Content-Security-Policy erlaubt nur eigene Skripte; Inline-Skripte gibt es nicht."""
    html = client.get(reverse("accounts:login")).content.decode()

    assert "<script>" not in html
    assert "vendor/htmx-2.0.4.min.js" in html
