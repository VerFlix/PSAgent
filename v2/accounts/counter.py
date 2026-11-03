"""Anmeldung an der Theke mit persönlichem QR-Code und PIN (#126).

Festlegung (Felix, 31.10. und 01.11.2026):

- Nur an Vereinsgeräten der Art „Theke“ (`ClubDevice.counter`). Dort gelten höchstens
  Verleiher-Rechte; Admin, PSA-Sachkundige und Viewer melden sich dort gar nicht an.
- Der QR-Code enthält nur eine zufällige Kennung (`secrets`, 256 Bit), keinen Namen; gespeichert
  ist nur ihre SHA-256. Ein neuer QR-Code macht den alten ungültig.
- Die PIN hat 6 Ziffern und liegt nur als Hash vor (Djangos Passwort-Hasher). Nach fünf falschen
  PINs ist sie gesperrt, bis die Person eine neue setzt oder der Admin sie löscht.
- PIN und QR-Code richtet man nicht in einer Sitzung ein, die selbst per PIN begann.

Keine eigene Kryptografie (CLAUDE.md, Regel 6): `secrets`, `hashlib.sha256` und Djangos Hasher.
"""

import hashlib
import secrets
from dataclasses import dataclass
from itertools import pairwise

from django.contrib.auth.hashers import check_password, make_password
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils import timezone

from accounts.models import (
    ClubDevice,
    CounterCredential,
    CounterEvent,
    CounterEventKind,
    User,
)
from accounts.rights import Right, may_use, require

QR_PREFIX = "PSAGENT-THEKE:"
PIN_LENGTH = 6
MAX_FAILURES = 5
PIN_SESSION = "psagent_counter_pin"  # Sitzung begann mit QR-Code und PIN
PIN_DEVICE = "psagent_counter_device"  # an diesem Theke-Gerät; nur dort gilt die Sitzung
WRONG = "QR-Code oder PIN passt nicht."


class CounterError(ValueError):
    """Anmeldung oder Einrichtung an der Theke abgewiesen; der Text sagt, warum."""


def _sha(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _log(
    user: User, kind: str, *, actor: User | None = None, device: ClubDevice | None = None
) -> None:
    CounterEvent.objects.create(user=user, kind=kind, actor=actor, device=device)


def weak_pin(pin: str) -> bool:
    """Leicht zu erratende PINs: lauter gleiche Ziffern oder auf- bzw. absteigend."""
    digits = [int(char) for char in pin]
    steps = {b - a for a, b in pairwise(digits)}
    return len(set(digits)) == 1 or steps in ({1}, {-1})


def _own(user: User, actor: User | None) -> None:
    require(actor, Right.COUNTER_CREDENTIALS)
    if actor is not None and actor.pk != user.pk:
        raise PermissionDenied("PIN und QR-Code richtet jede Person nur für sich selbst ein.")
    if actor is None:
        require(user, Right.COUNTER_CREDENTIALS)


def _credential(user: User) -> CounterCredential:
    credential, _ = CounterCredential.objects.select_for_update().get_or_create(user=user)
    return credential


@transaction.atomic
def set_pin(user: User, pin: str, *, actor: User | None) -> None:
    """Setzt die eigene PIN; hebt eine Sperre auf."""
    _own(user, actor)
    if len(pin) != PIN_LENGTH or not pin.isdigit():
        raise CounterError(f"Die PIN hat genau {PIN_LENGTH} Ziffern.")
    if weak_pin(pin):
        raise CounterError("Diese PIN ist zu leicht zu erraten; bitte eine andere wählen.")
    credential = _credential(user)
    credential.pin_hash = make_password(pin)
    credential.failed_attempts = 0
    credential.locked_at = None
    credential.save()
    _log(user, CounterEventKind.PIN_SET, actor=actor)


@transaction.atomic
def issue_qr(user: User, *, actor: User | None) -> str:
    """Neuer QR-Code für die eigene Person; der alte gilt danach nicht mehr. Gibt den Inhalt
    zurück, der nur einmal angezeigt bzw. gedruckt wird."""
    _own(user, actor)
    token = secrets.token_urlsafe(32)
    credential = _credential(user)
    credential.qr_hash = _sha(token)
    credential.save()
    _log(user, CounterEventKind.QR_ISSUED, actor=actor)
    return QR_PREFIX + token


@transaction.atomic
def clear_pin(user: User, *, actor: User | None) -> None:
    """Admin: PIN löschen (z. B. nach einer Sperre); die Person setzt danach eine neue."""
    require(actor, Right.MANAGE_USERS)
    credential = _credential(user)
    credential.pin_hash = ""
    credential.failed_attempts = 0
    credential.locked_at = None
    credential.save()
    _log(user, CounterEventKind.PIN_CLEARED, actor=actor)


@transaction.atomic
def revoke_qr(user: User, *, actor: User | None) -> None:
    """Admin: QR-Code widerrufen (z. B. Karte verloren)."""
    require(actor, Right.MANAGE_USERS)
    credential = _credential(user)
    credential.qr_hash = ""
    credential.save()
    _log(user, CounterEventKind.QR_REVOKED, actor=actor)


@dataclass(frozen=True)
class _Outcome:
    user: User | None
    error: str = ""


def _check(device: ClubDevice, token: str, pin: str) -> _Outcome:
    credential = (
        CounterCredential.objects.select_for_update()
        .select_related("user")
        .filter(qr_hash=_sha(token))
        .exclude(qr_hash="")
        .first()
    )
    if credential is None:
        return _Outcome(None, WRONG)
    user = credential.user
    if not user.is_active:
        return _Outcome(None, WRONG)
    if not may_use(user, Right.COUNTER_LOGIN):
        return _Outcome(
            None,
            "An der Theke melden sich nur Verleiher und Trainer/Materialwart an; bitte ein "
            "Verleiher-Konto nutzen.",
        )
    if credential.locked_at is not None:
        return _Outcome(
            None, "Die PIN ist gesperrt. Bitte am PC eine neue setzen oder den Admin fragen."
        )
    if not credential.pin_hash:
        return _Outcome(None, "Für dieses Konto ist keine PIN gesetzt; bitte am PC eine setzen.")
    if check_password(pin, credential.pin_hash):
        credential.failed_attempts = 0
        credential.save(update_fields=["failed_attempts"])
        _log(user, CounterEventKind.LOGIN, device=device)
        return _Outcome(user)
    credential.failed_attempts += 1
    left = MAX_FAILURES - credential.failed_attempts
    if left <= 0:
        credential.locked_at = timezone.now()
        credential.save(update_fields=["failed_attempts", "locked_at"])
        _log(user, CounterEventKind.LOCKED, device=device)
        return _Outcome(None, "Fünfmal falsch: Die PIN ist jetzt gesperrt.")
    credential.save(update_fields=["failed_attempts"])
    _log(user, CounterEventKind.FAILED, device=device)
    return _Outcome(None, f"Falsche PIN. Noch {left} Versuch{'e' if left > 1 else ''}.")


def authenticate(device: ClubDevice | None, code: str, pin: str) -> User:
    """Prüft QR-Code und PIN an einem Theke-Gerät; Fehlversuche bleiben gespeichert."""
    if device is None or not device.counter or device.revoked_at is not None:
        raise CounterError("Mit QR-Code und PIN meldet man sich nur an der Theke an.")
    token = code.strip().removeprefix(QR_PREFIX).strip()
    if not token:
        raise CounterError(WRONG)
    with transaction.atomic():
        outcome = _check(device, token, pin.strip())
    if outcome.user is None:
        raise CounterError(outcome.error)
    return outcome.user
