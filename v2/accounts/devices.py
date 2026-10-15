"""Vereinsgeräte: kurze Sitzungen und Abmelden nach jedem Vorgang (Konzept, Abschnitt 5).

Ein Admin legt ein Gerät einmalig als Vereinsgerät fest. Das Gerät bekommt ein signiertes
Cookie mit Geräte-ID und Zufallswert (Django-Signatur, kein eigenes Verfahren); in der
Datenbank steht nur der SHA-256 des Zufallswerts. Widerrufene Geräte gelten sofort wieder als
normale Geräte.
"""

import hashlib
import secrets

from django.conf import settings
from django.contrib.auth import logout
from django.core import signing
from django.http import HttpRequest, HttpResponse
from django.utils import timezone

from accounts.models import ClubDevice, User

SALT = "psagent.vereinsgeraet"
# Das Cookie gilt fünf Jahre; widerrufen wird über die Datenbank.
COOKIE_MAX_AGE = 5 * 365 * 24 * 60 * 60


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def register_device(name: str, *, actor: User, response: HttpResponse) -> ClubDevice:
    """Legt das aktuelle Gerät als Vereinsgerät an und setzt das Cookie."""
    token = secrets.token_urlsafe(32)
    device = ClubDevice.objects.create(name=name.strip(), token_hash=_hash(token), created_by=actor)
    response.set_cookie(
        settings.PSAGENT_CLUB_DEVICE_COOKIE,
        signing.dumps({"id": device.pk, "token": token}, salt=SALT),
        max_age=COOKIE_MAX_AGE,
        secure=settings.SESSION_COOKIE_SECURE,
        httponly=True,
        samesite="Strict",
    )
    return device


def revoke_device(device: ClubDevice) -> None:
    if device.revoked_at is None:
        device.revoked_at = timezone.now()
        device.save(update_fields=["revoked_at"])


def identify(request: HttpRequest) -> ClubDevice | None:
    """Das Vereinsgerät, von dem die Anfrage kommt, oder None."""
    raw = request.COOKIES.get(settings.PSAGENT_CLUB_DEVICE_COOKIE)
    if not raw:
        return None
    try:
        data = signing.loads(raw, salt=SALT)
    except signing.BadSignature:
        return None
    if not isinstance(data, dict):
        return None
    device_id, token = data.get("id"), data.get("token")
    if not isinstance(device_id, int) or not isinstance(token, str):
        return None
    device = ClubDevice.objects.filter(pk=device_id, revoked_at__isnull=True).first()
    if device is None or not secrets.compare_digest(device.token_hash, _hash(token)):
        return None
    return device


def finish_operation(request: HttpRequest) -> bool:
    """Nach einem abgeschlossenen Vorgang: am Vereinsgerät abmelden. True, wenn abgemeldet."""
    if getattr(request, "club_device", None) is None:
        return False
    logout(request)
    return True
