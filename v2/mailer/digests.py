"""Gemeinsames der Sammelmails an Vereinsadressen (#128 überfällige Ausleihen, #141 Sperren).

Empfänger prüft `clean_recipients` (höchstens `MAX_RECIPIENTS`, ohne Doppelte). Je Empfänger geht
eine eigene Mail raus, damit niemand die anderen Adressen sieht. Fehlermeldungen nennen nur
Anzahlen und Fehlerart, keine Adresse.
"""

import logging
from collections.abc import Sequence

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.mail import EmailMessage
from django.core.validators import validate_email
from django.urls import reverse

logger = logging.getLogger(__name__)

MAX_RECIPIENTS = 10


class DigestError(RuntimeError):
    """Einstellung ungültig oder Versand gescheitert; der Text sagt, warum (ohne Adresse)."""


def clean_recipients(addresses: Sequence[str]) -> list[str]:
    """Leerzeichen weg, Doppelte (ohne Groß-/Kleinschreibung) weg, jede Adresse geprüft."""
    result: list[str] = []
    seen: set[str] = set()
    for raw in addresses:
        address = raw.strip()
        if not address or address.lower() in seen:
            continue
        try:
            validate_email(address)
        except ValidationError as error:
            raise DigestError(f"„{address}“ ist keine gültige E-Mail-Adresse.") from error
        seen.add(address.lower())
        result.append(address)
    if len(result) > MAX_RECIPIENTS:
        raise DigestError(f"Höchstens {MAX_RECIPIENTS} Adressen.")
    return result


def absolute_link(name: str, *args: object) -> str:
    return str(settings.PSAGENT_BASE_URL).rstrip("/") + reverse(name, args=args)


def send_each(subject: str, body: str, addresses: Sequence[str]) -> None:
    """Je Adresse eine Mail; scheitert eine, folgt nach allen Versuchen ein `DigestError`."""
    failed = 0
    for address in addresses:
        try:
            EmailMessage(subject=subject, body=body, to=[address]).send()
        except Exception as error:
            failed += 1
            logger.warning("Sammelmail nicht gesendet: %s", type(error).__name__)
    if failed:
        raise DigestError(f"Sammelmail an {failed} von {len(addresses)} Adresse(n) gescheitert.")
