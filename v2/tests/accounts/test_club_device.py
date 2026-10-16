"""Vereinsgerät: kurze Sitzung, Abmelden nach jedem Vorgang; Einrichtungslink; Client-IP (#11)."""

import json
from datetime import timedelta
from io import StringIO
from typing import Any

import pytest
from django.conf import settings
from django.contrib.sessions.middleware import SessionMiddleware
from django.core.management import CommandError, call_command
from django.test import Client, RequestFactory, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.devices import finish_operation
from accounts.enrollment import create_token
from accounts.models import ClubDevice, EnrollmentToken, Passkey, Role, User
from accounts.net import client_ip
from tests.accounts.conftest import MakeUser
from tests.accounts.softkey import SoftKey

pytestmark = pytest.mark.django_db
COOKIE = settings.PSAGENT_CLUB_DEVICE_COOKIE


@pytest.fixture
def admin(make_user: MakeUser) -> User:
    return make_user(Role.ADMIN)


def make_device(client: Client, admin: User, name: str = "Tablet Ausgabe") -> ClubDevice:
    client.force_login(admin)
    client.post(reverse("accounts:club_device_register"), {"name": name})
    return ClubDevice.objects.get(name=name)


def test_admin_registers_club_device(client: Client, admin: User) -> None:
    device = make_device(client, admin)

    cookie = client.cookies[COOKIE]
    assert cookie["httponly"] is True
    assert cookie["samesite"] == "Strict"
    assert device.token_hash not in cookie.value  # nur der Hash steht in der Datenbank
    assert "Vereinsgerät" in client.get(reverse("home")).content.decode()


@pytest.mark.parametrize("role", [Role.PPE_EXPERT, Role.TRAINER, Role.LENDER, Role.VIEWER])
def test_only_admin_registers_devices(role: Role, client: Client, make_user: MakeUser) -> None:
    """Verbotener Fall: andere Rollen dürfen kein Vereinsgerät festlegen."""
    client.force_login(make_user(role))

    response = client.post(reverse("accounts:club_device_register"), {"name": "Tablet"})

    assert response.status_code == 403
    assert not ClubDevice.objects.exists()
    assert COOKIE not in client.cookies


def test_session_on_club_device_is_short(client: Client, admin: User, make_user: MakeUser) -> None:
    make_device(client, admin)
    client.post(reverse("accounts:logout"))
    lender = make_user(Role.LENDER)
    client.force_login(lender)

    client.get(reverse("home"))

    assert client.session.get_expiry_age() == settings.PSAGENT_CLUB_DEVICE_IDLE_SECONDS == 180


def test_idle_session_on_club_device_expires(
    client: Client, admin: User, make_user: MakeUser
) -> None:
    make_device(client, admin)
    client.force_login(make_user(Role.LENDER))
    client.get(reverse("home"))
    session = client.session
    session.set_expiry(timezone.now() - timedelta(seconds=1))
    session.save()

    assert client.get(reverse("home")).status_code == 302


def test_normal_device_keeps_eight_hours(client: Client, make_user: MakeUser) -> None:
    client.force_login(make_user(Role.LENDER))

    client.get(reverse("home"))

    assert client.session.get_expiry_age() == 8 * 60 * 60


def test_finish_operation_logs_out_on_club_device(client: Client, admin: User) -> None:
    device = make_device(client, admin)
    request = RequestFactory().get("/")
    SessionMiddleware(lambda r: None).process_request(request)  # type: ignore[arg-type]
    request.user = admin
    request.club_device = device  # type: ignore[attr-defined]

    assert finish_operation(request) is True
    assert not request.user.is_authenticated


def test_finish_operation_keeps_session_elsewhere(admin: User) -> None:
    request = RequestFactory().get("/")
    request.user = admin
    request.club_device = None  # type: ignore[attr-defined]

    assert finish_operation(request) is False
    assert request.user == admin


def test_revoked_device_is_normal_again(client: Client, admin: User) -> None:
    """Eine laufende Sitzung behält die kurze Frist; die nächste Anmeldung ist normal."""
    device = make_device(client, admin)
    client.post(reverse("accounts:club_device_revoke", args=[device.pk]))
    client.logout()
    client.force_login(admin)

    client.get(reverse("home"))

    assert client.session.get_expiry_age() == 8 * 60 * 60
    assert ClubDevice.objects.get().revoked_at is not None


@pytest.mark.parametrize("cookie", ["kaputt", "1:abc", ""])
def test_forged_cookie_is_ignored(cookie: str, client: Client, admin: User) -> None:
    make_device(client, admin)
    client.cookies[COOKIE] = cookie

    client.get(reverse("home"))

    assert client.session.get_expiry_age() == 8 * 60 * 60


# --- Einrichtungslink ---


def enroll(client: Client, token: str, key: SoftKey) -> Any:
    options = client.post(reverse("accounts:enroll_options", args=[token])).json()
    return client.post(
        reverse("accounts:enroll_finish", args=[token]),
        data=json.dumps({"credential": key.register(options), "name": "YubiKey"}),
        content_type="application/json",
    )


def test_enrollment_link_sets_up_first_passkey(client: Client, admin: User) -> None:
    token = create_token(admin)

    response = enroll(client, token, SoftKey())

    assert response.status_code == 200
    assert Passkey.objects.get().user == admin
    assert client.session["_auth_user_id"] == str(admin.pk)
    assert EnrollmentToken.objects.get().used_at is not None


def test_enrollment_link_works_only_once(client: Client, admin: User) -> None:
    token = create_token(admin)
    enroll(client, token, SoftKey())
    client.logout()

    assert client.get(reverse("accounts:enroll", args=[token])).status_code == 404
    assert client.post(reverse("accounts:enroll_options", args=[token])).status_code == 404
    assert Passkey.objects.count() == 1


def test_failed_enrollment_keeps_link_unused(client: Client, admin: User) -> None:
    """Scheitert die Einrichtung, bleibt der Link gültig und es entsteht kein Passkey."""
    token = create_token(admin)
    lazy = SoftKey(user_verified=False)
    options = client.post(reverse("accounts:enroll_options", args=[token])).json()

    response = client.post(
        reverse("accounts:enroll_finish", args=[token]),
        data=json.dumps({"credential": lazy.register(options), "name": "YubiKey"}),
        content_type="application/json",
    )

    assert response.status_code == 400
    assert not Passkey.objects.exists()
    assert EnrollmentToken.objects.get().used_at is None
    assert enroll(client, token, SoftKey()).status_code == 200


def test_second_enrollment_with_same_link_is_rejected(client: Client, admin: User) -> None:
    """Zwei Einrichtungen mit demselben Link: die zweite scheitert, es bleibt ein Passkey."""
    token = create_token(admin)
    first = Client()
    second = Client()
    options_first = first.post(reverse("accounts:enroll_options", args=[token])).json()
    options_second = second.post(reverse("accounts:enroll_options", args=[token])).json()

    def finish(c: Client, options: dict[str, Any]) -> Any:
        return c.post(
            reverse("accounts:enroll_finish", args=[token]),
            data=json.dumps({"credential": SoftKey().register(options), "name": "YubiKey"}),
            content_type="application/json",
        )

    assert finish(first, options_first).status_code == 200
    assert finish(second, options_second).status_code == 404
    assert Passkey.objects.count() == 1


def test_expired_enrollment_link_is_rejected(client: Client, admin: User) -> None:
    token = create_token(admin)
    EnrollmentToken.objects.update(expires_at=timezone.now() - timedelta(minutes=1))

    assert client.get(reverse("accounts:enroll", args=[token])).status_code == 404


def test_enrollment_link_is_stored_as_hash(admin: User) -> None:
    token = create_token(admin)

    assert EnrollmentToken.objects.get().token_hash != token
    assert token not in EnrollmentToken.objects.get().token_hash


def test_command_creates_enrollment_link(admin: User) -> None:
    out = StringIO()

    call_command("passkey_einrichtung", admin.username, stdout=out)

    assert "/einrichten/" in out.getvalue()
    assert EnrollmentToken.objects.filter(user=admin).count() == 1


def test_command_rejects_unknown_user(db: None) -> None:
    with pytest.raises(CommandError, match="Kein aktives Konto"):
        call_command("passkey_einrichtung", "gibt-es-nicht")


# --- Client-IP hinter Caddy ---


@override_settings(PSAGENT_TRUSTED_PROXIES=["10.0.0.2"])
def test_client_ip_behind_trusted_proxy() -> None:
    request = RequestFactory().get(
        "/", REMOTE_ADDR="10.0.0.2", HTTP_X_FORWARDED_FOR="1.2.3.4, 100.64.0.7"
    )

    assert client_ip(request) == "100.64.0.7"


@override_settings(PSAGENT_TRUSTED_PROXIES=[])
def test_forwarded_header_is_ignored_without_trusted_proxy() -> None:
    request = RequestFactory().get("/", REMOTE_ADDR="100.64.0.9", HTTP_X_FORWARDED_FOR="1.2.3.4")

    assert client_ip(request) == "100.64.0.9"
