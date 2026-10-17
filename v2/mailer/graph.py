"""Django-Mail-Backend für Microsoft Graph `sendMail` (#72).

Warum nicht SMTP: Microsoft schaltet die Anmeldung mit Passwort für SMTP AUTH in Exchange Online
Ende 2026 standardmäßig ab. Graph mit einer App-Registrierung braucht kein Passwort.

- **Anmeldung:** App-Registrierung in Entra, Anmeldung mit Zertifikat über `msal` (Microsofts
  Bibliothek; signiert die Anmeldung mit dem privaten Schlüssel). Schlüssel und IDs kommen nur
  aus der Umgebung (`.env.example`); der Schlüssel liegt als Datei auf dem Pi, nie im Repo.
- **Nur ein Postfach:** Gesendet wird immer als `DEFAULT_FROM_EMAIL`. Das Recht dafür gibt
  „RBAC for Applications“ in Exchange Online, beschränkt auf dieses Postfach; in Entra bekommt
  die App kein `Mail.Send` (Runbook `docs/runbooks/mail-m365.md`).
- **Keine Kopie:** `saveToSentItems` ist aus, damit das Postfach keine Personendaten sammelt.
- **Nur Text**, keine Anhänge.

Fehlermeldungen nennen nur Fehlercode bzw. HTTP-Status, keine Antworttexte von Microsoft.
Eingetragen in `MAILERS` (Django 6.1), im Betrieb als Standard (`psagent/settings/prod.py`).
"""

import json
from collections.abc import Sequence
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

import msal
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.mail import EmailMessage, EmailMultiAlternatives
from django.core.mail.backends.base import BaseEmailBackend

GRAPH = "https://graph.microsoft.com/v1.0"
SCOPE = "https://graph.microsoft.com/.default"
TIMEOUT = 15


class GraphError(RuntimeError):
    """Versand über Microsoft Graph fehlgeschlagen."""


def _setting(name: str) -> str:
    value = str(getattr(settings, name, "") or "").strip()
    if not value:
        raise ImproperlyConfigured(f"{name} fehlt für den Versand über Microsoft 365.")
    return value


@lru_cache(maxsize=1)
def _application() -> Any:
    """msal-Anwendung, einmal je Prozess; sie hält das Zugriffstoken bis zum Ablauf vor."""
    tenant = _setting("PSAGENT_GRAPH_TENANT_ID")
    client_id = _setting("PSAGENT_GRAPH_CLIENT_ID")
    key_path = Path(_setting("PSAGENT_GRAPH_CERT_PATH"))
    thumbprint = _setting("PSAGENT_GRAPH_CERT_THUMBPRINT")
    key = key_path.read_text()
    return msal.ConfidentialClientApplication(
        client_id,
        authority=f"https://login.microsoftonline.com/{tenant}",
        client_credential={"private_key": key, "thumbprint": thumbprint},
    )


def _addresses(values: Sequence[str]) -> list[dict[str, dict[str, str]]]:
    return [{"emailAddress": {"address": value}} for value in values]


def payload(message: EmailMessage) -> dict[str, Any]:
    if message.attachments or (
        isinstance(message, EmailMultiAlternatives) and message.alternatives
    ):
        raise GraphError("Über Microsoft 365 gehen nur Text-Mails ohne Anhänge.")
    reply_to = message.reply_to or settings.PSAGENT_MAIL_REPLY_TO
    return {
        "message": {
            "subject": message.subject,
            "body": {"contentType": "Text", "content": message.body},
            "toRecipients": _addresses(message.to),
            "ccRecipients": _addresses(message.cc),
            "bccRecipients": _addresses(message.bcc),
            "replyTo": _addresses(reply_to),
        },
        "saveToSentItems": False,
    }


class GraphEmailBackend(BaseEmailBackend):
    """Sendet jede Nachricht einzeln über `POST /users/{Absender}/sendMail`."""

    def send_messages(self, email_messages: Sequence[EmailMessage]) -> int:
        """Bricht beim ersten Fehler ab; der Aufrufer meldet ihn (die Quittung ist nachholbar)."""
        for message in email_messages:
            self._send(message)
        return len(email_messages)

    def _token(self) -> str:
        result = _application().acquire_token_for_client(scopes=[SCOPE])
        token = result.get("access_token")
        if not token:
            raise GraphError(f"Anmeldung bei Microsoft 365 fehlgeschlagen ({result.get('error')}).")
        return str(token)

    def _send(self, message: EmailMessage) -> None:
        data = json.dumps(payload(message)).encode()
        sender = quote(_setting("DEFAULT_FROM_EMAIL"), safe="")
        request = Request(  # noqa: S310 – feste https-Adresse von Microsoft Graph
            f"{GRAPH}/users/{sender}/sendMail",
            data=data,
            headers={
                "Authorization": f"Bearer {self._token()}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=TIMEOUT) as response:  # noqa: S310 – siehe oben
                status = response.status
        except HTTPError as error:
            raise GraphError(
                f"Microsoft 365 hat den Versand abgelehnt (HTTP {error.code})."
            ) from None
        except URLError as error:
            raise GraphError("Microsoft 365 ist nicht erreichbar.") from error
        if status != 202:
            raise GraphError(f"Microsoft 365 hat unerwartet geantwortet (HTTP {status}).")
