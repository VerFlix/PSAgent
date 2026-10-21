"""Nutzerverwaltung: jede Änderung mit YubiKey bestätigen (#95).

Festlegungen (Felix, 21.10.2026): jede Änderung einzeln; nur ein Passkey mit geprüfter
Herstellerbescheinigung von Yubico; ohne YubiKey nur die Verwaltungsbefehle auf dem Server.
Ohne Bestätigung (403, nichts ändert sich) prüft `tests/test_url_rights.py` für jede Adresse.
"""

import json
from io import StringIO
from typing import Any

import pytest
from django.core.management import CommandError, call_command
from django.test import Client
from django.urls import get_resolver, reverse
from django.utils import timezone

from accounts.confirm import FIELD
from accounts.models import Passkey, Qualification, Role, User
from accounts.rights import Right
from tests.accounts.conftest import MakeUser
from tests.accounts.softkey import FakeFidoRoot, SoftKey
from tests.test_url_rights import PROTECTED, row_counts, walk
from tests.yubikey_client import (
    YubiKeyClient,
    give_yubikey,
    login_with_yubikey,
    needs_confirmation,
)

pytestmark = pytest.mark.django_db

CONFIRMED = [(name, url) for name, url, _ in PROTECTED if needs_confirmation(url)]
# Lesen und die Optionen selbst brauchen keine Bestätigung. Die Sicherung (ZIP) hängt am selben
# Recht, gehört aber nicht zur Nutzerverwaltung und ändert nichts.
NOT_CONFIRMED = {"accounts:users", "accounts:user_detail", "accounts:yubikey_options", "backup"}


def options(client: Client, path: str) -> Any:
    return Client.post(
        client,
        reverse("accounts:yubikey_options"),
        data=json.dumps({"path": path}),
        content_type="application/json",
    )


def signed(client: Client, key: SoftKey, path: str) -> str:
    """Bestätigung mit `key` auf die Optionen, die der Server für `path` ausgibt."""
    response = options(client, path)
    assert response.status_code == 200, response.content
    return json.dumps(key.sign_in(response.json()))


@pytest.fixture
def admin(make_user: MakeUser, client: YubiKeyClient) -> User:
    user = make_user(Role.ADMIN)
    login_with_yubikey(client, user)
    return user


# --- Welche Adressen eine Bestätigung verlangen ---


def test_every_change_in_user_management_needs_confirmation() -> None:
    """Jede Adresse mit „Nutzer verwalten“ außer Liste, Detailseite, Optionen und Sicherung."""
    managed = {
        name
        for name, entry in walk(get_resolver().url_patterns)
        if getattr(entry.callback, "psagent_right", None) == Right.MANAGE_USERS
    }

    assert managed - NOT_CONFIRMED == {name for name, _ in CONFIRMED}
    assert {"accounts:certificate_register", "accounts:club_device_register"} <= managed


# --- Erlaubt ---


def test_confirmed_change_takes_effect(
    client: YubiKeyClient, admin: User, make_user: MakeUser
) -> None:
    expert = make_user(Role.PPE_EXPERT)
    today = timezone.localdate()

    response = client.post(
        reverse("accounts:qualification_grant", args=[expert.pk]),
        {
            "valid_from": today.isoformat(),
            "valid_until": today.replace(year=today.year + 1).isoformat(),
            "evidence": "Lehrgang (Testdaten)",
        },
    )

    assert response.status_code == 302
    assert Qualification.objects.filter(user=expert).exists()
    assert Passkey.objects.get(user=admin).last_used_at is not None


# --- Verboten ---


@pytest.mark.parametrize(("name", "url"), CONFIRMED)
@pytest.mark.parametrize(
    "case", ["Handy-Passkey", "fremder YubiKey", "widerrufen", "andere Adresse", "zweimal"]
)
def test_invalid_confirmation_is_refused(
    name: str, url: str, case: str, client: YubiKeyClient, admin: User, make_user: MakeUser
) -> None:
    """Je Adresse: nur eine frische Bestätigung mit dem eigenen YubiKey für genau diese Adresse."""
    key = client.key
    assert key is not None
    if case == "Handy-Passkey":
        phone = SoftKey()
        Passkey.objects.create(
            user=admin, name="Handy", credential_id=phone.credential_id, public_key=phone.cose_key()
        )
        assertion = signed(client, phone, url)
    elif case == "fremder YubiKey":
        assertion = signed(client, give_yubikey(make_user(Role.ADMIN)), url)
    elif case == "widerrufen":
        assertion = signed(client, key, url)
        Passkey.objects.filter(user=admin).update(revoked_at=timezone.now())
    elif case == "andere Adresse":
        other = reverse("accounts:user_create")
        if other == url:
            other = reverse("accounts:club_device_register")
        assertion = signed(client, key, other)
    else:
        assertion = signed(client, key, url)
        Client.post(client, url, {FIELD: assertion, "name": "Tablet"})
    before = row_counts()

    response = Client.post(client, url, {FIELD: assertion, "name": "Tablet", "reason": "Test"})

    assert response.status_code == 403, name
    assert row_counts() == before


def test_admin_without_yubikey_gets_no_options(client: Client, make_user: MakeUser) -> None:
    """Handy-Passkey allein reicht nicht; die Oberfläche sagt, was fehlt."""
    admin = make_user(Role.ADMIN)
    phone = SoftKey()
    Passkey.objects.create(
        user=admin, name="Handy", credential_id=phone.credential_id, public_key=phone.cose_key()
    )
    client.force_login(admin)

    response = options(client, reverse("accounts:user_create"))

    assert response.status_code == 400
    assert "braucht dieses Konto einen YubiKey" in response.json()["error"]


@pytest.mark.parametrize("path", ["/gibt-es-nicht/", "/konto/", ""])
def test_options_only_for_addresses_that_need_confirmation(
    path: str, client: YubiKeyClient, admin: User
) -> None:
    response = options(client, path)

    assert response.status_code == 400


def test_options_do_not_grant_the_change(client: YubiKeyClient, make_user: MakeUser) -> None:
    """Optionen bekommt jedes Konto mit YubiKey; die Änderung selbst bleibt Sache des Admins."""
    expert = make_user(Role.PPE_EXPERT)
    login_with_yubikey(client, expert)
    before = row_counts()

    response = client.post(reverse("accounts:user_create"), {"username": "x", "role": "admin"})

    assert response.status_code == 403
    assert row_counts() == before


def test_account_page_marks_attested_yubikey(client: YubiKeyClient, admin: User) -> None:
    page = client.get(reverse("accounts:account")).content.decode()

    assert "(YubiKey, bescheinigt)" in page


# --- Weiterer Passkey nur mit freigegebenem YubiKey ---


def register_passkey(client: Client, key: SoftKey, **extra: Any) -> Any:
    options = Client.post(client, reverse("accounts:passkey_register_options")).json()
    return Client.post(
        client,
        reverse("accounts:passkey_register"),
        data=json.dumps({"credential": key.register(options), "name": "Neu", **extra}),
        content_type="application/json",
    )


def test_hijacked_session_cannot_add_own_yubikey(
    client: YubiKeyClient, admin: User, fido_root: FakeFidoRoot
) -> None:
    """Angriff: Mit übernommener Sitzung den eigenen (echten) YubiKey eintragen."""
    response = register_passkey(client, fido_root.yubikey())

    assert response.status_code == 403
    assert Passkey.objects.filter(user=admin).count() == 1


def test_further_yubikey_with_confirmation_is_approved(
    client: YubiKeyClient, admin: User, fido_root: FakeFidoRoot
) -> None:
    assert client.key is not None
    path = reverse("accounts:passkey_register")

    response = register_passkey(
        client, fido_root.yubikey(), **{FIELD: json.loads(signed(client, client.key, path))}
    )

    assert response.status_code == 200
    new = Passkey.objects.get(user=admin, name="Neu")
    assert new.yubikey_attested and new.yubikey_approved_at is not None


def test_first_yubikey_needs_approval_on_the_server(
    client: Client, make_user: MakeUser, fido_root: FakeFidoRoot
) -> None:
    admin = make_user(Role.ADMIN)
    client.force_login(admin)

    assert register_passkey(client, fido_root.yubikey()).status_code == 200
    passkey = Passkey.objects.get(user=admin)
    assert passkey.yubikey_attested and passkey.yubikey_approved_at is None
    response = options(client, reverse("accounts:user_create"))
    assert response.status_code == 400
    assert "noch nicht freigegeben" in response.json()["error"]

    out = StringIO()
    call_command("yubikey_freigeben", admin.username, stdout=out)
    assert f"{passkey.pk}: Neu" in out.getvalue()
    passkey.refresh_from_db()
    assert passkey.yubikey_approved_at is None

    call_command("yubikey_freigeben", admin.username, "--passkey", str(passkey.pk), stdout=out)
    passkey.refresh_from_db()
    assert passkey.yubikey_approved_at is not None
    assert options(client, reverse("accounts:user_create")).status_code == 200


def test_command_approves_only_attested_keys(client: Client, make_user: MakeUser) -> None:
    admin = make_user(Role.ADMIN)
    phone = SoftKey()
    passkey = Passkey.objects.create(
        user=admin, name="Handy", credential_id=phone.credential_id, public_key=phone.cose_key()
    )

    with pytest.raises(CommandError, match="Kein freizugebender YubiKey"):
        call_command("yubikey_freigeben", admin.username, "--passkey", str(passkey.pk))

    passkey.refresh_from_db()
    assert passkey.yubikey_approved_at is None
