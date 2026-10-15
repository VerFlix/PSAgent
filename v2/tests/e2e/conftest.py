"""Gemeinsames für die Browser-Tests (Playwright, Marker `e2e`).

Die Tests laufen gegen den Live-Server von pytest-django. Passkeys kommen von einem virtuellen
Authenticator des Browsers (Chrome DevTools Protocol), nicht von Hardware.
"""

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from django.test import override_settings
from playwright.sync_api import BrowserType, Page
from pytest_django.live_server_helper import LiveServer

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
