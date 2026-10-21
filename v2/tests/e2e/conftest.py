"""Gemeinsames für die Browser-Tests (Playwright, Marker `e2e`).

Die Tests laufen gegen den Live-Server von pytest-django. Passkeys kommen von einem virtuellen
Authenticator des Browsers (Chrome DevTools Protocol), nicht von Hardware.

- `sign_in(page, role)` meldet eine Person direkt per Sitzungs-Cookie an. Der Weg über die
  Anmeldeseite hat eigene Tests; Ablauf-Tests sparen sich so das Durchklicken.
- `add_yubikey(page, user)` steckt einen virtuellen YubiKey an, mit dem `user` Änderungen in der
  Nutzerverwaltung bestätigt (#95).
- `device_page` öffnet eine Seite als Desktop, Handy oder Tablet (#13: mobile Ansicht);
  `assert_fits_width` prüft, dass nichts seitlich übersteht.
- Jeder Oberflächen-PR bringt seinen Browser-Test mit, für Verleih und Prüfmodus auch auf
  Handy und Tablet.
"""

import base64
import os
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from django.conf import settings
from django.test import Client, override_settings
from playwright.sync_api import Browser, BrowserType, Page, Playwright
from pytest_django.live_server_helper import LiveServer

from accounts.models import Role, User
from tests.yubikey_client import give_yubikey

# Playwright betreibt im Testprozess eine Ereignisschleife; Django-Datenbankzugriffe aus den
# Tests selbst sind trotzdem synchron und sicher.
os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

# In Cloud-Sitzungen ist Chromium vorinstalliert, aber ggf. in einer anderen Version als die,
# die Playwright erwartet. Dann wird dieses Chromium benutzt.
FALLBACK_CHROMIUM = Path("/opt/pw-browsers/chromium")


@pytest.fixture(scope="session")
def browser_type_launch_args(
    browser_type_launch_args: dict[str, Any], browser_type: BrowserType
) -> dict[str, Any]:
    args = dict(browser_type_launch_args)
    if not Path(browser_type.executable_path).exists() and FALLBACK_CHROMIUM.exists():
        args["executable_path"] = str(FALLBACK_CHROMIUM)
    return args


@pytest.fixture
def site(live_server: LiveServer) -> Iterator[str]:
    """Adresse des Live-Servers; Passkeys gelten für genau diese Origin."""
    url = live_server.url.replace("127.0.0.1", "localhost")
    with override_settings(PSAGENT_WEBAUTHN_RP_ID="localhost", PSAGENT_WEBAUTHN_ORIGINS=[url]):
        yield url


@pytest.fixture
def authenticator(page: Page) -> str:
    """Virtueller FIDO2-Schlüssel mit PIN-Prüfung, wie ein YubiKey."""
    cdp = page.context.new_cdp_session(page)
    cdp.send("WebAuthn.enable")
    result = cdp.send(
        "WebAuthn.addVirtualAuthenticator",
        {
            "options": {
                "protocol": "ctap2",
                "transport": "usb",
                "hasResidentKey": True,
                "hasUserVerification": True,
                "isUserVerified": True,
                "automaticPresenceSimulation": True,
            }
        },
    )
    return str(result["authenticatorId"])


# Geräte für die mobile Ansicht; immer mit Chromium, auch wo Playwright WebKit vorschlägt.
DEVICES = {"Desktop": None, "Handy": "Pixel 7", "Tablet": "iPad Mini"}


@pytest.fixture(params=list(DEVICES))
def device_page(
    request: pytest.FixtureRequest, browser: Browser, playwright: Playwright
) -> Iterator[Page]:
    """Eine Seite je Geräteprofil; der Testname zeigt das Gerät (z. B. `[Handy]`)."""
    name = DEVICES[request.param]
    profile = dict(playwright.devices[name]) if name else {}
    profile.pop("default_browser_type", None)
    context = browser.new_context(**profile)
    yield context.new_page()
    context.close()


def sign_in(page: Page, site: str, role: Role = Role.LENDER, **fields: Any) -> User:
    """Legt ein Konto an und meldet es im Browser an, ohne die Anmeldeseite zu benutzen."""
    defaults = {"username": f"e2e-{role}", "first_name": "Testperson", "last_name": role.label}
    user = User.objects.create_user(role=role, **{**defaults, **fields})
    client = Client()
    client.force_login(user)
    page.context.add_cookies(
        [
            {
                "name": settings.SESSION_COOKIE_NAME,
                "value": client.cookies[settings.SESSION_COOKIE_NAME].value,
                "url": site,
            }
        ]
    )
    return user


def assert_fits_width(page: Page) -> None:
    """Auf dem Gerät steht nichts seitlich über (kein waagerechtes Scrollen der Seite)."""
    overflow = page.evaluate(
        "document.documentElement.scrollWidth - document.documentElement.clientWidth"
    )
    assert overflow <= 0, f"Seite ist {overflow}px breiter als der Bildschirm: {page.url}"


def add_yubikey(page: Page, user: User) -> Callable[[], None]:
    """Virtueller Schlüssel im Browser mit einem YubiKey-Passkey von `user` (Bescheinigung geprüft).

    Den Passkey trägt der Test direkt ein; den privaten Schlüssel bekommt nur der virtuelle
    Authenticator des Browsers. So bestätigt der Browser wie mit echter Hardware. Zurück kommt
    eine Funktion, die den Passkey wieder aus dem virtuellen Schlüssel nimmt.
    """
    key = give_yubikey(user)
    cdp = page.context.new_cdp_session(page)
    cdp.send("WebAuthn.enable")
    authenticator = cdp.send(
        "WebAuthn.addVirtualAuthenticator",
        {
            "options": {
                "protocol": "ctap2",
                "transport": "usb",
                "hasResidentKey": True,
                "hasUserVerification": True,
                "isUserVerified": True,
                "automaticPresenceSimulation": True,
            }
        },
    )["authenticatorId"]
    private = key.private_key.private_bytes(
        serialization.Encoding.DER,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    cdp.send(
        "WebAuthn.addCredential",
        {
            "authenticatorId": authenticator,
            "credential": {
                "credentialId": base64.b64encode(key.credential_id).decode(),
                "isResidentCredential": True,
                "rpId": "localhost",
                "privateKey": base64.b64encode(private).decode(),
                "userHandle": base64.b64encode(user.webauthn_id.bytes).decode(),
                "signCount": 0,
            },
        },
    )

    def remove() -> None:
        cdp.send(
            "WebAuthn.removeCredential",
            {
                "authenticatorId": authenticator,
                "credentialId": base64.b64encode(key.credential_id).decode(),
            },
        )

    return remove
