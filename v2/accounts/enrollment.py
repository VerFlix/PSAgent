"""Einmal-Links zum Einrichten des ersten Passkeys.

Admin und PSA-Sachkundige können sich nicht mit Passwort anmelden. Ihren ersten Passkey
richten sie über einen Link ein, den ein Verwaltungsbefehl auf dem Server erzeugt
(`manage.py passkey_einrichtung`). Der Link gilt einmal und nur kurz; gespeichert ist nur
der SHA-256 des Tokens.
"""

import hashlib
import secrets
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from accounts.models import EnrollmentToken, User


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_token(user: User, *, hours: int | None = None) -> str:
    """Erzeugt einen Einrichtungslink für `user` und gibt den Token zurück (nur einmal sichtbar)."""
    token = secrets.token_urlsafe(32)
    valid = timedelta(hours=hours or settings.PSAGENT_ENROLLMENT_HOURS)
    EnrollmentToken.objects.create(
        user=user, token_hash=_hash(token), expires_at=timezone.now() + valid
    )
    return token


def find_token(token: str) -> EnrollmentToken | None:
    """Der gültige, unbenutzte Einrichtungslink zu `token`, sonst None."""
    return (
        EnrollmentToken.objects.select_related("user")
        .filter(
            token_hash=_hash(token),
            used_at__isnull=True,
            expires_at__gt=timezone.now(),
            user__is_active=True,
        )
        .first()
    )


@transaction.atomic
def use_token(entry: EnrollmentToken) -> bool:
    """Markiert den Link als benutzt. False, wenn ihn jemand gleichzeitig benutzt hat."""
    return (
        EnrollmentToken.objects.filter(pk=entry.pk, used_at__isnull=True).update(
            used_at=timezone.now()
        )
        == 1
    )
