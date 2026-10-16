"""Nutzer, Rollen und Sachkunde verwalten (#67). Nur mit dem Recht „Nutzer verwalten“ (Admin).

Jede Änderung an Rolle und Aktiv-Status steht im nur anhängbaren Protokoll `AccountEvent`,
mit Begründung. Schutzregeln:

- Ein Admin nimmt sich nicht selbst die Admin-Rolle und deaktiviert sich nicht selbst.
- Das letzte aktive Admin-Konto bleibt erhalten, auch bei Verwaltungsbefehlen.
- Zugang bekommt ein Konto nur über einen Einrichtungslink; kein Passwort läuft über den Admin.

`actor=None` steht für Verwaltungsbefehle auf dem Server (Notzugang).
"""

from datetime import date

from django.db import transaction
from django.utils import timezone

from accounts import enrollment
from accounts.models import (
    AccountEvent,
    AccountEventKind,
    EnrollmentToken,
    Passkey,
    Qualification,
    QualificationRevocation,
    Role,
    User,
)
from accounts.rights import Right, require

# Gültigkeit der Einrichtungslinks aus der Verwaltung: Zeit genug, den Link persönlich zu
# übergeben. Der Notzugang per Befehl gilt kürzer (PSAGENT_ENROLLMENT_HOURS).
INVITATION_HOURS = 72


class AccountError(ValueError):
    """Ein Verwaltungsvorgang verstößt gegen eine Regel. Die Meldung ist für die Oberfläche."""


def _reason(reason: str) -> str:
    text = reason.strip()
    if not text:
        raise AccountError("Bitte eine Begründung angeben.")
    return text


def _log(
    user: User,
    kind: AccountEventKind,
    *,
    actor: User | None,
    reason: str = "",
    old_role: str = "",
    new_role: str = "",
) -> AccountEvent:
    return AccountEvent.objects.create(
        user=user, kind=kind, actor=actor, reason=reason, old_role=old_role, new_role=new_role
    )


def _lock(user: User) -> User:
    return User.objects.select_for_update().get(pk=user.pk)


def _keep_last_admin(user: User) -> None:
    """Weist ab, wenn `user` das letzte aktive Admin-Konto ist.

    Alle aktiven Admin-Zeilen werden gesperrt, damit zwei gleichzeitige Vorgänge nicht beide
    ein anderes Admin-Konto übrig glauben.
    """
    admins = list(
        User.objects.select_for_update()
        .filter(role=Role.ADMIN, is_active=True)
        .values_list("pk", flat=True)
    )
    if admins == [user.pk]:
        raise AccountError("Das ist das letzte aktive Admin-Konto; es bleibt erhalten.")


def _expire_open_links(user: User) -> None:
    EnrollmentToken.objects.filter(
        user=user, used_at__isnull=True, expires_at__gt=timezone.now()
    ).update(expires_at=timezone.now())


# --- Konto und Einrichtungslink ---


@transaction.atomic
def create_account(
    username: str, *, first_name: str, last_name: str, role: Role, actor: User | None
) -> tuple[User, str]:
    """Legt ein Konto ohne Passwort an und stellt den Einrichtungslink aus (Token nur hier)."""
    require(actor, Right.MANAGE_USERS)
    username = username.strip()
    if not username:
        raise AccountError("Bitte einen Benutzernamen angeben.")
    if User.objects.filter(username__iexact=username).exists():
        raise AccountError(f"Der Benutzername „{username}“ ist schon vergeben.")
    user = User.objects.create_user(
        username=username,
        first_name=first_name.strip(),
        last_name=last_name.strip(),
        role=Role(role),
    )
    _log(user, AccountEventKind.CREATED, actor=actor, new_role=user.role)
    return user, issue_enrollment(user, actor=actor)


@transaction.atomic
def issue_enrollment(user: User, *, actor: User | None, hours: int = INVITATION_HOURS) -> str:
    """Neuer Einrichtungslink; noch offene Links des Kontos werden damit ungültig."""
    require(actor, Right.MANAGE_USERS)
    user = _lock(user)
    if not user.is_active:
        raise AccountError(f"{user} ist deaktiviert; bitte zuerst wieder aktivieren.")
    _expire_open_links(user)
    token = enrollment.create_token(user, hours=hours)
    _log(user, AccountEventKind.ENROLLMENT_ISSUED, actor=actor)
    return token


# --- Rolle und Aktiv-Status ---


@transaction.atomic
def change_role(user: User, role: Role, *, actor: User | None, reason: str) -> User:
    """Ändert die Rolle. Wird daraus Admin oder PSA-Sachkundig, gilt sofort die Passkey-Pflicht."""
    require(actor, Right.MANAGE_USERS)
    reason = _reason(reason)
    user = _lock(user)
    new = Role(role)
    if user.role == new:
        raise AccountError(f"{user} hat bereits die Rolle {new.label}.")
    if user.role == Role.ADMIN:
        if actor is not None and actor.pk == user.pk:
            raise AccountError("Die eigene Admin-Rolle ändert ein anderer Admin.")
        if user.is_active:
            _keep_last_admin(user)
    old = user.role
    user.role = new
    user.save(update_fields=["role"])
    # Laufende Sitzungen enden: Eine Passwort-Sitzung darf nach der Beförderung zu Admin oder
    # PSA-Sachkundig nicht weiterlaufen, und neue Rechte gelten erst nach neuer Anmeldung.
    user.end_sessions()
    _log(
        user, AccountEventKind.ROLE_CHANGED, actor=actor, reason=reason, old_role=old, new_role=new
    )
    return user


@transaction.atomic
def deactivate(user: User, *, actor: User | None, reason: str) -> User:
    """Deaktiviert ein Konto: laufende Sitzungen und offene Einrichtungslinks enden sofort."""
    require(actor, Right.MANAGE_USERS)
    reason = _reason(reason)
    user = _lock(user)
    if not user.is_active:
        raise AccountError(f"{user} ist bereits deaktiviert.")
    if actor is not None and actor.pk == user.pk:
        raise AccountError("Das eigene Konto deaktiviert ein anderer Admin.")
    if user.role == Role.ADMIN:
        _keep_last_admin(user)
    user.is_active = False
    user.save(update_fields=["is_active"])
    _expire_open_links(user)
    _log(user, AccountEventKind.DEACTIVATED, actor=actor, reason=reason)
    return user


@transaction.atomic
def reactivate(user: User, *, actor: User | None, reason: str) -> User:
    require(actor, Right.MANAGE_USERS)
    reason = _reason(reason)
    user = _lock(user)
    if user.is_active:
        raise AccountError(f"{user} ist bereits aktiv.")
    user.is_active = True
    user.save(update_fields=["is_active"])
    _log(user, AccountEventKind.REACTIVATED, actor=actor, reason=reason)
    return user


# --- Sachkunde ---


@transaction.atomic
def grant_qualification(
    user: User, *, valid_from: date, valid_until: date, evidence: str, actor: User | None
) -> Qualification:
    """Trägt eine Sachkunde ein. Verlängern heißt: neuer Eintrag."""
    require(actor, Right.MANAGE_USERS)
    if valid_until < valid_from:
        raise AccountError("„gültig bis“ liegt vor „gültig ab“.")
    if not evidence.strip():
        raise AccountError("Bitte den Nachweis angeben (z. B. Lehrgang, Anbieter, Datum).")
    return Qualification.objects.create(
        user=user,
        valid_from=valid_from,
        valid_until=valid_until,
        evidence=evidence.strip(),
        created_by=actor,
    )


@transaction.atomic
def revoke_qualification(
    qualification: Qualification, *, actor: User | None, reason: str
) -> QualificationRevocation:
    """Widerruft eine Sachkunde ab sofort; der Eintrag selbst bleibt unverändert."""
    require(actor, Right.MANAGE_USERS)
    reason = _reason(reason)
    Qualification.objects.select_for_update().get(pk=qualification.pk)
    if QualificationRevocation.objects.filter(qualification=qualification).exists():
        raise AccountError("Diese Sachkunde ist bereits widerrufen.")
    return QualificationRevocation.objects.create(
        qualification=qualification, reason=reason, created_by=actor
    )


# --- Passkeys ---


@transaction.atomic
def revoke_passkey(passkey: Passkey, *, actor: User | None, reason: str) -> Passkey:
    """Widerruft einen Passkey eines anderen Kontos, etwa bei verlorenem Schlüssel.

    Anders als beim Widerruf im eigenen Konto auch den letzten: Ein verlorener Schlüssel muss
    sofort ungültig werden. Danach hilft ein neuer Einrichtungslink. Nur den eigenen letzten
    Passkey nimmt sich ein Admin nicht, sonst sperrt er sich aus.
    """
    require(actor, Right.MANAGE_USERS)
    reason = _reason(reason)
    passkey = Passkey.objects.select_for_update().select_related("user").get(pk=passkey.pk)
    if passkey.revoked_at is not None:
        raise AccountError("Dieser Passkey ist bereits widerrufen.")
    if actor is not None and actor.pk == passkey.user_id:
        active = Passkey.objects.filter(user=passkey.user, revoked_at__isnull=True)
        if passkey.user.requires_passkey and active.exclude(pk=passkey.pk).count() == 0:
            raise AccountError(
                "Das ist Ihr letzter Passkey. Bitte zuerst einen weiteren einrichten."
            )
    passkey.revoked_at = timezone.now()
    passkey.save(update_fields=["revoked_at"])
    passkey.user.end_sessions()  # auch eine mit dem gestohlenen Schlüssel geöffnete Sitzung
    _log(
        passkey.user,
        AccountEventKind.PASSKEY_REVOKED,
        actor=actor,
        reason=f"{reason} (Passkey „{passkey.name}“)",
    )
    return passkey
