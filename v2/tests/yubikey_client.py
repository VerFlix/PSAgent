"""Test-Client, der Änderungen in der Nutzerverwaltung mit simuliertem YubiKey bestätigt (#95).

`login_with_yubikey(client, user)` meldet an und gibt dem Konto einen Passkey mit geprüfter
Herstellerbescheinigung. Danach hängt der Client an jedes Formular an eine Adresse, die eine
Bestätigung verlangt, eine frische Bestätigung an, genau wie `static/js/passkey.js` im Browser.
Ohne diesen Aufruf sendet er unverändert; so lassen sich die verbotenen Fälle prüfen.
"""

import json
from typing import Any
from urllib.parse import urlsplit

from django.test import Client
from django.urls import Resolver404, resolve, reverse

from accounts.confirm import FIELD
from accounts.models import Passkey, User
from tests.accounts.softkey import SoftKey


def needs_confirmation(path: str) -> bool:
    try:
        return bool(getattr(resolve(urlsplit(path).path).func, "psagent_yubikey", False))
    except Resolver404:
        return False


class YubiKeyClient(Client):
    key: SoftKey | None = None

    def confirmation(self, path: str) -> str:
        """Bestätigung für genau eine Anfrage an `path`, wie sie der Browser mitschickt."""
        assert self.key is not None
        response = super().post(
            reverse("accounts:yubikey_options"),
            data=json.dumps({"path": urlsplit(path).path}),
            content_type="application/json",
        )
        assert response.status_code == 200, response.content
        return json.dumps(self.key.sign_in(response.json()))

    def post(self, path: str, data: Any = None, *args: Any, **kwargs: Any) -> Any:  # type: ignore[override]
        if (
            self.key is not None
            and needs_confirmation(path)
            and isinstance(data, dict | None)
            and FIELD not in (data or {})
        ):
            data = {**(data or {}), FIELD: self.confirmation(path)}
        return super().post(path, data, *args, **kwargs)


def give_yubikey(user: User, key: SoftKey | None = None) -> SoftKey:
    """Trägt einen Passkey ein, dessen Herstellerbescheinigung von Yubico geprüft ist."""
    key = key or SoftKey()
    Passkey.objects.create(
        user=user,
        name="YubiKey (Testdaten)",
        credential_id=key.credential_id,
        public_key=key.cose_key(),
        yubikey_attested=True,
    )
    return key


def login_with_yubikey(client: Client, user: User) -> SoftKey:
    """Meldet `user` an; der Client bestätigt danach Verwaltungsänderungen mit dessen YubiKey."""
    key = give_yubikey(user)
    client.force_login(user)
    assert isinstance(client, YubiKeyClient), "Bitte die Fixture `client` aus tests/conftest.py."
    client.key = key
    return key
