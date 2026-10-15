"""Passkeys: einrichten, anmelden, widerrufen; verbotene Fälle (#11).

Der Schlüssel ist simuliert (`tests/accounts/softkey.py`); geprüft wird im Server mit
py_webauthn wie bei echter Hardware.
"""

import json
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse

from accounts.models import Passkey, Role, User
from tests.accounts.conftest import MakeUser
from tests.accounts.softkey import SoftKey

pytestmark = pytest.mark.django_db


def post_json(client: Client, name: str, body: dict[str, Any] | None = None, **kwargs: Any) -> Any:
    return client.post(
        reverse(name, kwargs=kwargs or None),
        data=json.dumps(body or {}),
        content_type="application/json",
    )


def register(client: Client, key: SoftKey, name: str = "YubiKey blau") -> Any:
    options = post_json(client, "accounts:passkey_register_options").json()
    return post_json(
        client, "accounts:passkey_register", {"credential": key.register(options), "name": name}
    )


def sign_in(client: Client, key: SoftKey, **kwargs: Any) -> Any:
    options = post_json(client, "accounts:passkey_login_options").json()
    return post_json(
        client, "accounts:passkey_login", {"credential": key.sign_in(options, **kwargs)}
    )


@pytest.fixture
def user(make_user: MakeUser) -> User:
    return make_user(Role.LENDER)


@pytest.fixture
def key(client: Client, user: User) -> SoftKey:
    """Ein am Konto eingerichteter Schlüssel; danach ist der Client wieder abgemeldet."""
    soft = SoftKey()
    client.force_login(user)
    assert register(client, soft).status_code == 200
    client.logout()
    return soft


def test_register_and_sign_in(client: Client, user: User, key: SoftKey) -> None:
    passkey = Passkey.objects.get(user=user)
    assert passkey.name == "YubiKey blau"
    assert passkey.transports == ["usb"]

    response = sign_in(client, key)

    assert response.status_code == 200
    assert response.json()["redirect"] == reverse("home")
    assert client.session["_auth_user_id"] == str(user.pk)
    passkey.refresh_from_db()
    assert passkey.last_used_at is not None
    assert passkey.sign_count == 1


def test_several_passkeys_per_account(client: Client, user: User, key: SoftKey) -> None:
    second = SoftKey()
    client.force_login(user)
    register(client, second, "Handy")
    client.logout()

    assert sign_in(client, second).status_code == 200
    assert user.passkeys.count() == 2


def test_unknown_passkey_is_rejected(client: Client, key: SoftKey) -> None:
    """Verbotener Fall: fremder Passkey."""
    stranger = SoftKey(user_handle=key.user_handle)

    response = sign_in(client, stranger)

    assert response.status_code == 400
    assert "_auth_user_id" not in client.session


def test_revoked_passkey_is_rejected(client: Client, user: User, key: SoftKey) -> None:
    """Verbotener Fall: widerrufener Passkey."""
    client.force_login(user)
    client.post(reverse("accounts:passkey_revoke", args=[Passkey.objects.get().pk]))
    client.logout()

    response = sign_in(client, key)

    assert response.status_code == 400
    assert "_auth_user_id" not in client.session


def test_inactive_account_cannot_use_passkey(client: Client, user: User, key: SoftKey) -> None:
    user.is_active = False
    user.save()

    assert sign_in(client, key).status_code == 400


def test_signature_from_other_key_is_rejected(client: Client, key: SoftKey) -> None:
    """Gleiche Credential-ID, aber falscher privater Schlüssel."""
    forged = SoftKey(credential_id=key.credential_id, user_handle=key.user_handle)

    assert sign_in(client, forged).status_code == 400


def test_wrong_origin_is_rejected(client: Client, key: SoftKey) -> None:
    """Phishing-Schutz: Antwort für eine fremde Seite."""
    assert sign_in(client, key, origin="https://psagent.example.org").status_code == 400


def test_user_verification_is_required(client: Client, user: User) -> None:
    lazy = SoftKey(user_verified=False)
    client.force_login(user)

    assert register(client, lazy).status_code == 400
    assert not Passkey.objects.exists()


def test_cloned_key_is_rejected(client: Client, key: SoftKey) -> None:
    """Sinkt der Signaturzähler, ist der Schlüssel womöglich geklont."""
    assert sign_in(client, key, sign_count=10).status_code == 200
    client.logout()

    assert sign_in(client, key, sign_count=5).status_code == 400


def test_user_handle_must_match(client: Client, key: SoftKey, make_user: MakeUser) -> None:
    other = make_user(Role.LENDER)

    response = sign_in(client, key, user_handle=other.webauthn_id.bytes)

    assert response.status_code == 400


def test_challenge_is_used_only_once(client: Client, key: SoftKey) -> None:
    """Eine Antwort gilt nur einmal, auch bei Schlüsseln ohne Signaturzähler (Zähler bleibt 0)."""
    options = post_json(client, "accounts:passkey_login_options").json()
    credential = key.sign_in(options, sign_count=0)
    first = post_json(client, "accounts:passkey_login", {"credential": credential})

    replay = post_json(client, "accounts:passkey_login", {"credential": credential})

    assert first.status_code == 200
    assert replay.status_code == 400


def test_sign_in_without_options_is_rejected(client: Client, key: SoftKey) -> None:
    options = {"rpId": "localhost", "challenge": "AAAA"}

    response = post_json(client, "accounts:passkey_login", {"credential": key.sign_in(options)})

    assert response.status_code == 400


def test_same_key_cannot_be_registered_twice(client: Client, user: User, key: SoftKey) -> None:
    client.force_login(user)

    response = register(client, key)

    assert response.status_code == 400
    assert user.passkeys.count() == 1


def test_registration_needs_login(client: Client) -> None:
    response = post_json(client, "accounts:passkey_register_options")

    assert response.status_code == 302
    assert not Passkey.objects.exists()


def test_cannot_revoke_foreign_passkey(client: Client, key: SoftKey, make_user: MakeUser) -> None:
    other = make_user(Role.ADMIN)
    client.force_login(other)

    response = client.post(reverse("accounts:passkey_revoke", args=[Passkey.objects.get().pk]))

    assert response.status_code == 404
    assert Passkey.objects.get().revoked_at is None


@pytest.mark.parametrize("role", [Role.ADMIN, Role.PPE_EXPERT])
def test_last_passkey_of_admin_cannot_be_revoked(
    role: Role, client: Client, make_user: MakeUser
) -> None:
    admin = make_user(role)
    client.force_login(admin)
    register(client, SoftKey())

    client.post(reverse("accounts:passkey_revoke", args=[Passkey.objects.get().pk]))

    assert Passkey.objects.get().revoked_at is None


def test_admin_signs_in_with_passkey(client: Client, make_user: MakeUser) -> None:
    admin = make_user(Role.ADMIN)
    soft = SoftKey()
    client.force_login(admin)
    register(client, soft)
    client.logout()

    assert sign_in(client, soft).status_code == 200
    assert client.session["_auth_user_id"] == str(admin.pk)


def test_account_page_lists_passkeys(client: Client, user: User, key: SoftKey) -> None:
    client.force_login(user)

    html = client.get(reverse("accounts:account")).content.decode()

    assert "YubiKey blau" in html
    assert "Vereinsgeräte" not in html  # nur für Admin


def test_sign_in_without_user_verification_is_rejected(client: Client, key: SoftKey) -> None:
    """PIN bzw. Biometrie ist auch bei der Anmeldung Pflicht."""
    key.user_verified = False

    response = sign_in(client, key)

    assert response.status_code == 400
    assert "_auth_user_id" not in client.session
