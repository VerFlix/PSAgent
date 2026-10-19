"""Zeitstempel nach RFC 3161 von einem externen Dienst (#16).

Der Dienst signiert einen Hash zusammen mit seiner Uhrzeit, mit seinem eigenen Schlüssel. Wer
später die Hash-Kette neu berechnet, bekommt nur Stempel mit dem Datum von heute; rückdatieren
kann er nicht. Gestempelt wird der Kettenkopf selbst (`ChainLink.hash` als SHA-256-Wert).

Anfordern und Prüfen übernimmt pyHanko (CLAUDE.md, Regel 6). Vertraut wird nur der Zertifizierungs-
stelle aus `PSAGENT_TSA_CA_PATH` (PEM); welcher Dienst das ist, erprobt Felix lokal.
"""

import asyncio
from datetime import datetime
from pathlib import Path

from asn1crypto import cms, pem, x509
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from pyhanko.sign.timestamps import TimeStamper
from pyhanko.sign.timestamps.requests_client import RequestsHTTPTimeStamper
from pyhanko.sign.validation.generic_cms import validate_tst_signed_data
from pyhanko_certvalidator import ValidationContext

ALGORITHM = "sha256"
TIMEOUT = 15


class TimestampError(RuntimeError):
    """Zeitstempel ließ sich nicht holen oder ist ungültig; der Text nennt den Grund."""


def _setting(name: str) -> str:
    value = str(getattr(settings, name, "") or "").strip()
    if not value:
        raise ImproperlyConfigured(f"{name} fehlt für Zeitstempel nach RFC 3161.")
    return value


def stamper() -> TimeStamper:
    """Der konfigurierte Dienst; in Tests ersetzt durch einen eigenen Dienst ohne Netz."""
    return RequestsHTTPTimeStamper(  # type: ignore[no-untyped-call]
        _setting("PSAGENT_TSA_URL"), timeout=TIMEOUT
    )


def trust_roots() -> list[x509.Certificate]:
    data = Path(_setting("PSAGENT_TSA_CA_PATH")).read_bytes()
    blocks = pem.unarmor(data, multiple=True) if pem.detect(data) else [(None, None, data)]
    return [x509.Certificate.load(der) for _, _, der in blocks]


def request(digest: bytes) -> bytes:
    """Holt einen Zeitstempel für den SHA-256-Wert `digest`; gibt das Token (DER) zurück."""
    service = stamper()  # fehlende Einstellung nennt ihren Namen
    try:
        token = asyncio.run(service.async_timestamp(digest, ALGORITHM))
    except Exception as error:
        raise TimestampError(
            f"Zeitstempeldienst nicht erreichbar oder Antwort ungültig ({type(error).__name__})."
        ) from error
    stamped: bytes = token.dump()
    check(stamped, digest)
    return stamped


def check(token: bytes, digest: bytes) -> datetime:
    """Prüft Signatur, Vertrauen und Hash des Tokens; gibt die Zeit des Stempels zurück."""
    try:
        content = cms.ContentInfo.load(token)
        signed_data = content["content"]
        status = asyncio.run(
            validate_tst_signed_data(
                signed_data,
                ValidationContext(trust_roots=trust_roots(), allow_fetching=False),
                lambda algorithm: digest if algorithm == ALGORITHM else b"",
            )
        )
    except ImproperlyConfigured:
        raise
    except Exception as error:
        raise TimestampError(f"Zeitstempel ist unlesbar ({type(error).__name__}).") from error
    if not status.get("intact") or not status.get("valid"):
        raise TimestampError("Zeitstempel gehört nicht zu diesem Hash oder wurde verändert.")
    if status.get("trust_problem_indic") is not None or status.get("validation_path") is None:
        raise TimestampError("Zeitstempel stammt nicht vom konfigurierten Dienst.")
    timestamp: datetime = status["timestamp"]
    return timestamp
