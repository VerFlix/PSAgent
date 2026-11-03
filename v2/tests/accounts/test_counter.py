"""Anmeldung an der Theke mit persönlichem QR-Code und PIN (#126).

Festlegung (Felix, 31.10. und 01.11.2026): nur an Vereinsgeräten der Art „Theke“; QR-Code mit
zufälliger Kennung (nur der Hash wird gespeichert) plus 6-stellige PIN; nach 5 falschen PINs
gesperrt. An der Theke gelten höchstens Verleiher-Rechte; Admin und PSA-Sachkundige melden sich
dort nicht an. Eigene PIN und eigenen QR-Code richtet man nicht in einer Sitzung per PIN ein.
"""

import hashlib

import pytest
from django.conf import settings
from django.contrib.auth.hashers import check_password
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.test import Client
from django.urls import reverse

from accounts import counter
from accounts.devices import register_device
from accounts.models import ClubDevice, CounterCredential, CounterEvent, Role, User
from core import chain
from tests.accounts.conftest import MakeUser
from tests.yubikey_client import login_with_yubikey

pytestmark = pytest.mark.django_db
COOKIE = settings.PSAGENT_CLUB_DEVICE_COOKIE
PIN = "482915"


def device_for(client: Client, admin: User, *, theke: bool = True) -> ClubDevice:
    """Macht den Test-Client zu einem Vereinsgerät (wie nach dem Festlegen durch den Admin)."""
    response = HttpResponse()
    device = register_device("Tablet Ausgabe", actor=admin, response=response, counter=theke)
    client.cookies[COOKIE] = response.cookies[COOKIE].value
    return device


@pytest.fixture
def admin(make_user: MakeUser) -> User:
    return make_user(Role.ADMIN)


@pytest.fixture
def lender(make_user: MakeUser) -> User:
    return make_user(Role.LENDER)


def ready(user: User) -> str:
    """PIN setzen und QR-Code ausgeben, wie es die Person selbst tut; gibt den QR-Inhalt zurück."""
    counter.set_pin(user, PIN, actor=user)
    return counter.issue_qr(user, actor=user)


def counter_device(admin: User) -> ClubDevice:
    return register_device("Tablet Ausgabe", actor=admin, response=HttpResponse(), counter=True)


# --- PIN und QR-Code einrichten ---


def test_lender_sets_pin_and_gets_qr_code(lender: User) -> None:
    code = ready(lender)

    token = code.removeprefix(counter.QR_PREFIX)
    credential = CounterCredential.objects.get(user=lender)
    assert code.startswith(counter.QR_PREFIX) and len(token) >= 43
    assert credential.qr_hash == hashlib.sha256(token.encode()).hexdigest()
    assert token not in str(CounterCredential.objects.values().get())
    assert PIN not in credential.pin_hash and check_password(PIN, credential.pin_hash)
    kinds = list(CounterEvent.objects.filter(user=lender).values_list("kind", flat=True))
    assert kinds == ["pin_set", "qr_issued"]
    assert chain.verify() == []


@pytest.mark.parametrize("pin", ["12345", "1234567", "12a456", "000000", "123456", "987654"])
def test_weak_or_malformed_pin_is_rejected(lender: User, pin: str) -> None:
    with pytest.raises(counter.CounterError):
        counter.set_pin(lender, pin, actor=lender)


def test_only_own_pin(lender: User, make_user: MakeUser) -> None:
    with pytest.raises(PermissionDenied):
        counter.set_pin(make_user(Role.LENDER), PIN, actor=lender)


@pytest.mark.parametrize("role", [Role.ADMIN, Role.PPE_EXPERT, Role.VIEWER])
def test_roles_outside_the_counter_get_no_pin(make_user: MakeUser, role: Role) -> None:
    user = make_user(role)

    with pytest.raises(PermissionDenied):
        counter.set_pin(user, PIN, actor=user)
    with pytest.raises(PermissionDenied):
        counter.issue_qr(user, actor=user)


# --- Anmelden ---


def test_qr_code_and_pin_log_in_at_the_counter(lender: User, admin: User) -> None:
    code = ready(lender)

    assert counter.authenticate(counter_device(admin), code, PIN) == lender
    assert CounterEvent.objects.filter(user=lender, kind="login").count() == 1


@pytest.mark.parametrize("theke", [False, None])
def test_only_at_counter_devices(lender: User, admin: User, theke: bool | None) -> None:
    code = ready(lender)
    device = None
    if theke is not None:
        device = register_device("PC Materialraum", actor=admin, response=HttpResponse())

    with pytest.raises(counter.CounterError, match="Theke"):
        counter.authenticate(device, code, PIN)


def test_five_wrong_pins_lock_until_a_new_pin(lender: User, admin: User) -> None:
    code, device = ready(lender), counter_device(admin)
    for left in (4, 3, 2, 1):
        with pytest.raises(counter.CounterError, match=f"Noch {left}"):
            counter.authenticate(device, code, "111999")

    with pytest.raises(counter.CounterError, match="gesperrt"):
        counter.authenticate(device, code, "111999")
    with pytest.raises(counter.CounterError, match="gesperrt"):
        counter.authenticate(device, code, PIN)
    assert CounterEvent.objects.filter(user=lender, kind="locked").count() == 1

    counter.set_pin(lender, "730264", actor=lender)
    assert counter.authenticate(device, code, "730264") == lender


def test_success_resets_the_failures(lender: User, admin: User) -> None:
    code, device = ready(lender), counter_device(admin)
    for _ in range(4):
        with pytest.raises(counter.CounterError):
            counter.authenticate(device, code, "111999")

    counter.authenticate(device, code, PIN)

    assert CounterCredential.objects.get(user=lender).failed_attempts == 0


def test_new_qr_code_invalidates_the_old_one(lender: User, admin: User) -> None:
    old = ready(lender)
    new = counter.issue_qr(lender, actor=lender)
    device = counter_device(admin)

    with pytest.raises(counter.CounterError, match="QR-Code oder PIN"):
        counter.authenticate(device, old, PIN)
    assert counter.authenticate(device, new, PIN) == lender


def test_unknown_code_counts_nothing(lender: User, admin: User) -> None:
    ready(lender)

    with pytest.raises(counter.CounterError, match="QR-Code oder PIN"):
        counter.authenticate(counter_device(admin), counter.QR_PREFIX + "x" * 43, PIN)

    assert CounterCredential.objects.get(user=lender).failed_attempts == 0


@pytest.mark.parametrize("role", [Role.ADMIN, Role.PPE_EXPERT, Role.VIEWER])
def test_other_roles_are_refused_even_with_a_credential(
    make_user: MakeUser, admin: User, role: Role
) -> None:
    """Etwa nach einem Rollenwechsel: Der QR-Code bleibt, die Theke weist trotzdem ab."""
    lender = make_user(Role.LENDER)
    code = ready(lender)
    User.objects.filter(pk=lender.pk).update(role=role)

    with pytest.raises(counter.CounterError, match="Verleiher"):
        counter.authenticate(counter_device(admin), code, PIN)
    assert CounterCredential.objects.get(user=lender).failed_attempts == 0


def test_inactive_account_is_refused(lender: User, admin: User) -> None:
    code = ready(lender)
    User.objects.filter(pk=lender.pk).update(is_active=False)

    with pytest.raises(counter.CounterError):
        counter.authenticate(counter_device(admin), code, PIN)


def test_admin_clears_pin_and_revokes_qr(lender: User, admin: User) -> None:
    code = ready(lender)
    device = counter_device(admin)

    counter.clear_pin(lender, actor=admin)
    with pytest.raises(counter.CounterError, match="keine PIN"):
        counter.authenticate(device, code, PIN)

    counter.set_pin(lender, PIN, actor=lender)
    counter.revoke_qr(lender, actor=admin)
    with pytest.raises(counter.CounterError, match="QR-Code oder PIN"):
        counter.authenticate(device, code, PIN)
    with pytest.raises(PermissionDenied):
        counter.clear_pin(lender, actor=lender)


# --- Rechte an der Theke ---


def test_counter_login_page_logs_in_and_caps_rights(
    client: Client, make_user: MakeUser, admin: User
) -> None:
    trainer = make_user(Role.TRAINER)
    code = ready(trainer)
    device_for(client, admin)

    response = client.post(reverse("accounts:counter_login"), {"code": code, "pin": PIN})

    assert response.status_code == 302
    assert client.session["_auth_user_id"] == str(trainer.pk)
    assert client.session[counter.PIN_SESSION] is True
    assert client.get(reverse("lending:overview")).status_code == 200
    assert client.get(reverse("inventory:product_create")).status_code == 403


def test_trainer_with_password_is_capped_at_the_counter_too(
    client: Client, make_user: MakeUser, admin: User
) -> None:
    trainer = make_user(Role.TRAINER)
    device_for(client, admin)
    client.force_login(trainer)

    assert client.get(reverse("inventory:product_create")).status_code == 403


def test_trainer_keeps_full_rights_on_other_devices(
    client: Client, make_user: MakeUser, admin: User
) -> None:
    trainer = make_user(Role.TRAINER)
    device_for(client, admin, theke=False)
    client.force_login(trainer)

    assert client.get(reverse("inventory:product_create")).status_code == 200


def test_expert_session_at_the_counter_is_ended(
    client: Client, make_user: MakeUser, admin: User
) -> None:
    device_for(client, admin)
    client.force_login(make_user(Role.PPE_EXPERT))

    response = client.get(reverse("lending:overview"))

    assert response.status_code == 302 and "_auth_user_id" not in client.session


def test_viewer_password_login_at_the_counter_is_refused(
    client: Client, make_user: MakeUser, admin: User
) -> None:
    viewer = make_user(Role.VIEWER)
    password = "ein-langes-Test-Passwort-42"  # gitleaks:allow – künstlich
    viewer.set_password(password)
    viewer.save()
    device_for(client, admin)

    response = client.post(
        reverse("accounts:login"),
        {"username": viewer.username, "password": password},
    )

    assert response.status_code == 200
    assert "An der Theke" in response.content.decode()
    assert "_auth_user_id" not in client.session


def test_pin_session_cannot_change_pin_or_qr(client: Client, lender: User, admin: User) -> None:
    code = ready(lender)
    device_for(client, admin)
    client.post(reverse("accounts:counter_login"), {"code": code, "pin": PIN})

    response = client.post(reverse("accounts:counter_pin"), {"pin": "730264", "repeat": "730264"})
    qr = client.post(reverse("accounts:counter_qr"))

    assert response.status_code == 403 and qr.status_code == 403
    assert counter.authenticate(counter_device(admin), code, PIN) == lender


def test_account_page_sets_pin_and_prints_card(client: Client, lender: User) -> None:
    client.force_login(lender)

    response = client.post(reverse("accounts:counter_pin"), {"pin": PIN, "repeat": PIN})
    card = client.post(reverse("accounts:counter_qr"))

    assert response.status_code == 302
    assert card.status_code == 200 and card["Content-Type"] == "application/pdf"
    assert bytes(card.content).startswith(b"%PDF")
    assert CounterCredential.objects.get(user=lender).qr_hash


def test_login_page_shows_counter_form_only_at_the_counter(client: Client, admin: User) -> None:
    assert "QR-Code" not in client.get(reverse("accounts:login")).content.decode()

    device_for(client, admin)

    assert "QR-Code" in client.get(reverse("accounts:login")).content.decode()


def test_admin_marks_device_as_counter_with_yubikey(client: Client, admin: User) -> None:
    login_with_yubikey(client, admin)

    client.post(reverse("accounts:club_device_register"), {"name": "Tablet", "theke": "on"})

    assert ClubDevice.objects.get(name="Tablet").counter is True


def test_admin_clears_pin_in_user_management(client: Client, admin: User, lender: User) -> None:
    ready(lender)
    login_with_yubikey(client, admin)

    response = client.post(reverse("accounts:user_counter", args=[lender.pk]), {"aktion": "pin"})

    assert response.status_code == 302
    assert CounterCredential.objects.get(user=lender).pin_hash == ""


def test_pin_session_cannot_manage_passkeys(client: Client, lender: User, admin: User) -> None:
    code = ready(lender)
    device_for(client, admin)
    client.post(reverse("accounts:counter_login"), {"code": code, "pin": PIN})

    assert client.post(reverse("accounts:passkey_register_options")).status_code == 403
    assert client.post(reverse("accounts:passkey_register")).status_code == 403
    assert client.post(reverse("accounts:passkey_revoke", args=[1])).status_code == 403


@pytest.mark.parametrize(
    "elsewhere", ["kein Vereinsgerät", "anderes Theke-Gerät", "kein Theke-Gerät"]
)
def test_pin_session_ends_away_from_its_counter_device(
    client: Client, lender: User, admin: User, elsewhere: str
) -> None:
    code = ready(lender)
    device_for(client, admin)
    client.post(reverse("accounts:counter_login"), {"code": code, "pin": PIN})
    assert client.get(reverse("lending:overview")).status_code == 200

    if elsewhere == "kein Vereinsgerät":
        del client.cookies[COOKIE]
    else:
        device_for(client, admin, theke=elsewhere == "anderes Theke-Gerät")
    response = client.get(reverse("lending:overview"))

    assert response.status_code == 302 and "_auth_user_id" not in client.session
