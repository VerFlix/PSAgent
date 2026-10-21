"""Benutzer, Rollen und die Qualifikation Sachkunde (Konzept, Abschnitt 5).

Rollen bauen aufeinander auf: Admin > PSA-Sachkundig > Trainer/Materialwart > Verleiher >
Viewer. Prüfen und Signieren hängt zusätzlich an der Qualifikation Sachkunde. Sie hat ein
Ablaufdatum und wird nicht durch die Rolle ersetzt: Ein Admin ohne gültige Sachkunde darf nicht
prüfen, ein Trainer mit Sachkunde auch nicht.

Sachkunde-Einträge und Widerrufe werden nur angehängt. Eine Verlängerung ist ein neuer Eintrag,
ein Widerruf ein eigener Eintrag mit Begründung. So bleibt nachweisbar, ob die Sachkunde zu einem
bestimmten Zeitpunkt gültig war, etwa beim Signieren einer Prüfungsrunde.
"""

import uuid
from datetime import datetime
from typing import Any, ClassVar

from django.contrib.auth.models import AbstractUser
from django.contrib.auth.models import UserManager as DjangoUserManager
from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.crypto import salted_hmac

from core.append_only import AppendOnlyModel


class Role(models.TextChoices):
    """Rollen, von der höchsten zur niedrigsten."""

    ADMIN = "admin", "Admin"
    PPE_EXPERT = "ppe_expert", "PSA-Sachkundig"
    TRAINER = "trainer", "Trainer/Materialwart"
    LENDER = "lender", "Verleiher"
    VIEWER = "viewer", "Viewer"


# Rangfolge von der niedrigsten zur höchsten Rolle.
ROLE_ORDER: tuple[Role, ...] = (
    Role.VIEWER,
    Role.LENDER,
    Role.TRAINER,
    Role.PPE_EXPERT,
    Role.ADMIN,
)
_RANK = {role: rank for rank, role in enumerate(ROLE_ORDER)}


class UserManager(DjangoUserManager["User"]):
    """Wie Djangos UserManager; ein Superuser bekommt die Rolle Admin."""

    def create_superuser(
        self,
        username: str,
        email: str | None = None,
        password: str | None = None,
        **extra_fields: Any,
    ) -> "User":
        extra_fields.setdefault("role", Role.ADMIN)
        return super().create_superuser(username, email, password, **extra_fields)


class User(AbstractUser):
    """Persönliches Konto. Gemeinsame Logins gibt es nicht (Konzept, Abschnitt 5)."""

    role = models.CharField("Rolle", max_length=20, choices=Role.choices, default=Role.VIEWER)
    stamp_text = models.TextField(
        "Briefkopf/Stempel",
        blank=True,
        help_text="Erscheint auf PDFs des Sachkundigen. Leer: der vollständige Name.",
    )

    webauthn_id = models.UUIDField(
        "Kennung für Passkeys",
        default=uuid.uuid4,
        unique=True,
        editable=False,
        help_text="Zufällige Kennung, die Passkeys statt des Benutzernamens speichern.",
    )

    session_epoch = models.PositiveIntegerField(
        "Sitzungsstand",
        default=0,
        editable=False,
        help_text="Erhöhen beendet alle laufenden Sitzungen des Kontos.",
    )

    objects: ClassVar[UserManager] = UserManager()

    class Meta:
        verbose_name = "Benutzer"
        verbose_name_plural = "Benutzer"
        constraints = (
            models.CheckConstraint(
                condition=Q(role__in=Role.values), name="accounts_user_role_valid"
            ),
        )

    def has_role(self, minimum: Role) -> bool:
        """True, wenn das Konto aktiv ist und mindestens die Rolle `minimum` hat."""
        return self.is_active and _RANK[Role(self.role)] >= _RANK[minimum]

    def has_valid_qualification(self, at: datetime | None = None) -> bool:
        """True, wenn zum Zeitpunkt `at` (Standard: jetzt) eine Sachkunde gültig war."""
        return Qualification.valid_at(at).filter(user=self).exists()

    def may_inspect(self, at: datetime | None = None) -> bool:
        """Prüfen und signieren: Rolle ab PSA-Sachkundig und gültige Sachkunde zum Zeitpunkt.

        Gilt auch für Entsperren, Aussondern, „keine PSA“ setzen, „nächste Prüfung“ setzen und
        PSA-relevante Felder ändern (Rechtetabelle, Konzept Abschnitt 5).
        """
        return self.has_role(Role.PPE_EXPERT) and self.has_valid_qualification(at)

    def _get_session_auth_hash(self, secret: str | None = None) -> str:
        """Wie in Django (HMAC über den Passwort-Hash), zusätzlich über den Sitzungsstand.

        Django bindet jede Sitzung an diesen Wert und beendet sie, sobald er sich ändert. Den
        Sitzungsstand erhöhen Rollenwechsel und Passkey-Widerruf (`end_sessions`); so enden
        auch Sitzungen, die ohne Passwort, also per Passkey, geöffnet wurden.
        """
        return salted_hmac(
            "psagent.accounts.User.get_session_auth_hash",
            f"{self.password}|{self.session_epoch}",
            secret=secret,
            algorithm="sha256",
        ).hexdigest()

    def end_sessions(self) -> None:
        """Beendet alle laufenden Sitzungen dieses Kontos beim nächsten Seitenaufruf."""
        User.objects.filter(pk=self.pk).update(session_epoch=models.F("session_epoch") + 1)
        self.refresh_from_db(fields=["session_epoch"])

    @property
    def requires_passkey(self) -> bool:
        """Admin und PSA-Sachkundige melden sich nur mit Passkey an (Konzept, Abschnitt 5)."""
        return self.role in (Role.ADMIN, Role.PPE_EXPERT)

    @property
    def stamp(self) -> str:
        """Briefkopf/Stempel für PDFs: eigener Text oder der vollständige Name."""
        return self.stamp_text or self.get_full_name()


class Qualification(AppendOnlyModel):
    """Sachkunde einer Person für einen Zeitraum. Wird nur angehängt, nie geändert.

    Gültig vom ersten bis einschließlich zum letzten Tag, nach deutscher Zeit. Ein
    Signaturzertifikat (`SigningCertificate`, #18) gilt nur zusammen mit gültiger Sachkunde.
    """

    user = models.ForeignKey(
        User, on_delete=models.PROTECT, related_name="qualifications", verbose_name="Person"
    )
    valid_from = models.DateField("gültig ab")
    valid_until = models.DateField("gültig bis einschließlich")
    evidence = models.TextField("Nachweis", help_text="z. B. Lehrgang, Anbieter und Datum")
    created_at = models.DateTimeField("angelegt am", default=timezone.now)
    created_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="+",
        null=True,
        blank=True,
        verbose_name="angelegt von",
    )

    class Meta:
        verbose_name = "Sachkunde"
        verbose_name_plural = "Sachkunde-Einträge"
        ordering = ("valid_from", "id")
        constraints = (
            models.CheckConstraint(
                condition=Q(valid_until__gte=models.F("valid_from")),
                name="accounts_qualification_period_valid",
            ),
        )

    def __str__(self) -> str:
        return f"Sachkunde {self.user} {self.valid_from} bis {self.valid_until}"

    @classmethod
    def valid_at(cls, at: datetime | None = None) -> "models.QuerySet[Qualification]":
        """Alle Einträge, die zum Zeitpunkt `at` gültig und nicht widerrufen waren."""
        moment = at or timezone.now()
        day = timezone.localdate(moment)
        return cls.objects.filter(valid_from__lte=day, valid_until__gte=day).exclude(
            revocation__revoked_at__lte=moment
        )


class QualificationRevocation(AppendOnlyModel):
    """Widerruf einer Sachkunde ab einem Zeitpunkt. Wird nur angehängt, nie geändert."""

    qualification = models.OneToOneField(
        Qualification,
        on_delete=models.PROTECT,
        related_name="revocation",
        verbose_name="Sachkunde",
    )
    revoked_at = models.DateTimeField("widerrufen ab", default=timezone.now)
    reason = models.TextField("Begründung")
    created_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="+",
        null=True,
        blank=True,
        verbose_name="widerrufen von",
    )

    class Meta:
        verbose_name = "Widerruf der Sachkunde"
        verbose_name_plural = "Widerrufe der Sachkunde"
        constraints = (
            models.CheckConstraint(
                condition=Q(reason__regex=r"\S"),
                name="accounts_revocation_reason_required",
            ),
        )

    def __str__(self) -> str:
        return f"Widerruf {self.qualification} ab {self.revoked_at}"


class SigningCertificate(AppendOnlyModel):
    """Zugelassenes Signaturzertifikat eines YubiKeys (#18). Wird nur angehängt, nie geändert.

    Die Liste bestimmt, wessen Signatur der Server annimmt. Aufnahme und Widerruf sind eigene
    Vorgänge in der Hash-Kette; die Regeln stehen in `accounts/certificates.py`. Gespeichert ist
    nur Öffentliches: das Zertifikat und die Herstellerbescheinigung (Attestation) von Yubico.
    """

    user = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="signing_certificates",
        verbose_name="Person",
    )
    name = models.CharField("Bezeichnung", max_length=100, help_text="z. B. YubiKey 1, Reserve")
    der = models.BinaryField("Zertifikat (DER)")
    sha256 = models.CharField("SHA-256-Fingerabdruck", max_length=64, unique=True)
    key_sha256 = models.CharField(
        "SHA-256 des öffentlichen Schlüssels",
        max_length=64,
        unique=True,
        help_text="Ein Schlüssel steht nur einmal in der Liste, auch mit neuem Zertifikat.",
    )
    subject = models.CharField("Inhaber laut Zertifikat", max_length=500)
    not_valid_before = models.DateTimeField("Zertifikat gültig ab")
    not_valid_after = models.DateTimeField("Zertifikat gültig bis")
    yubikey_serial = models.PositiveBigIntegerField("Seriennummer des YubiKeys")
    attestation = models.TextField(
        "Herstellerbescheinigung (PEM)",
        help_text="Bescheinigung für Slot 9c und die Zwischenzertifikate bis zu Yubico.",
    )
    created_at = models.DateTimeField("aufgenommen am", default=timezone.now)
    created_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="+",
        null=True,
        blank=True,
        verbose_name="aufgenommen von",
    )

    class Meta:
        verbose_name = "Signaturzertifikat"
        verbose_name_plural = "Signaturzertifikate"
        ordering = ("created_at", "id")
        constraints = (
            models.CheckConstraint(
                condition=Q(sha256__regex=r"^[0-9a-f]{64}$")
                & Q(key_sha256__regex=r"^[0-9a-f]{64}$"),
                name="accounts_certificate_sha256",
            ),
            models.CheckConstraint(
                condition=Q(name__regex=r"\S"), name="accounts_certificate_name_required"
            ),
            models.CheckConstraint(
                condition=Q(not_valid_after__gt=models.F("not_valid_before")),
                name="accounts_certificate_period_valid",
            ),
        )

    def __str__(self) -> str:
        return f"{self.name} ({self.user}, {self.sha256[:16]}…)"


class SigningCertificateRevocation(AppendOnlyModel):
    """Widerruf (Sperre) eines Signaturzertifikats ab einem Zeitpunkt. Nur angehängt.

    Der Zeitpunkt darf zurückliegen (z. B. seit wann ein YubiKey verloren ist), aber nicht in
    der Zukunft. Signaturen davor bleiben gültig, Signaturen ab dann nicht.
    """

    certificate = models.OneToOneField(
        SigningCertificate,
        on_delete=models.PROTECT,
        related_name="revocation",
        verbose_name="Zertifikat",
    )
    revoked_at = models.DateTimeField("widerrufen ab", default=timezone.now)
    reason = models.TextField("Begründung")
    created_at = models.DateTimeField("eingetragen am", default=timezone.now)
    created_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="+",
        null=True,
        blank=True,
        verbose_name="widerrufen von",
    )

    class Meta:
        verbose_name = "Widerruf eines Signaturzertifikats"
        verbose_name_plural = "Widerrufe von Signaturzertifikaten"
        constraints = (
            models.CheckConstraint(
                condition=Q(reason__regex=r"\S"),
                name="accounts_certificate_revocation_reason",
            ),
            models.CheckConstraint(
                condition=Q(revoked_at__lte=models.F("created_at")),
                name="accounts_certificate_revocation_not_future",
            ),
        )

    def __str__(self) -> str:
        return f"Widerruf {self.certificate} ab {self.revoked_at}"


class Passkey(models.Model):
    """Passkey (FIDO2/WebAuthn) eines Kontos. Mehrere je Konto; Widerruf statt Löschen."""

    user = models.ForeignKey(
        User, on_delete=models.PROTECT, related_name="passkeys", verbose_name="Konto"
    )
    name = models.CharField("Bezeichnung", max_length=100)
    credential_id = models.BinaryField("Credential-ID", unique=True)
    public_key = models.BinaryField("öffentlicher Schlüssel (COSE)")
    sign_count = models.PositiveBigIntegerField("Signaturzähler", default=0)
    transports = models.JSONField("Übertragungswege", default=list, blank=True)
    aaguid = models.CharField("AAGUID (Modell)", max_length=36, blank=True)
    yubikey_attested = models.BooleanField(
        "YubiKey (Herstellerbescheinigung geprüft)",
        default=False,
        help_text="Nur solche Passkeys bestätigen Änderungen in der Nutzerverwaltung (#95).",
    )
    created_at = models.DateTimeField("angelegt am", default=timezone.now)
    last_used_at = models.DateTimeField("zuletzt benutzt", null=True, blank=True)
    revoked_at = models.DateTimeField("widerrufen am", null=True, blank=True)

    class Meta:
        verbose_name = "Passkey"
        verbose_name_plural = "Passkeys"
        ordering = ("created_at", "id")
        constraints = (
            models.CheckConstraint(
                condition=Q(name__regex=r"\S"), name="accounts_passkey_name_required"
            ),
        )

    def __str__(self) -> str:
        return f"{self.name} ({self.user})"

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None


class EnrollmentToken(models.Model):
    """Einmal-Link zum Einrichten des ersten Passkeys. Gespeichert wird nur der Hash."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="+", verbose_name="Konto")
    token_hash = models.CharField("SHA-256 des Tokens", max_length=64, unique=True)
    created_at = models.DateTimeField("angelegt am", default=timezone.now)
    expires_at = models.DateTimeField("gültig bis")
    used_at = models.DateTimeField("benutzt am", null=True, blank=True)

    class Meta:
        verbose_name = "Einrichtungslink"
        verbose_name_plural = "Einrichtungslinks"

    def __str__(self) -> str:
        return f"Einrichtungslink für {self.user}"


class ClubDevice(models.Model):
    """Vereinsgerät (z. B. Tablet an der Ausgabe): kurze Sitzungen, Abmelden nach jedem Vorgang.

    Das Gerät trägt ein signiertes Cookie mit ID und Zufallswert; gespeichert ist nur der Hash.
    """

    name = models.CharField("Bezeichnung", max_length=100)
    token_hash = models.CharField("SHA-256 des Tokens", max_length=64, unique=True)
    created_at = models.DateTimeField("angelegt am", default=timezone.now)
    created_by = models.ForeignKey(
        User, on_delete=models.PROTECT, related_name="+", null=True, blank=True
    )
    revoked_at = models.DateTimeField("widerrufen am", null=True, blank=True)

    class Meta:
        verbose_name = "Vereinsgerät"
        verbose_name_plural = "Vereinsgeräte"
        constraints = (
            models.CheckConstraint(
                condition=Q(name__regex=r"\S"), name="accounts_clubdevice_name_required"
            ),
        )

    def __str__(self) -> str:
        return self.name


class AccountEventKind(models.TextChoices):
    CREATED = "created", "angelegt"
    ROLE_CHANGED = "role_changed", "Rolle geändert"
    DEACTIVATED = "deactivated", "deaktiviert"
    REACTIVATED = "reactivated", "wieder aktiviert"
    ENROLLMENT_ISSUED = "enrollment_issued", "Einrichtungslink ausgestellt"
    PASSKEY_REVOKED = "passkey_revoked", "Passkey widerrufen"
    STAMP_CHANGED = "stamp_changed", "Briefkopf/Stempel geändert"


# Vorgänge, die eine Begründung brauchen.
EVENTS_WITH_REASON = (
    AccountEventKind.ROLE_CHANGED,
    AccountEventKind.DEACTIVATED,
    AccountEventKind.REACTIVATED,
    AccountEventKind.PASSKEY_REVOKED,
)


class AccountEvent(AppendOnlyModel):
    """Protokoll der Kontoverwaltung (#67). Wird nur angehängt, nie geändert.

    So bleibt nachweisbar, welche Rolle eine Person zu einem Zeitpunkt hatte und wer sie
    vergeben hat. `actor` ist leer bei Verwaltungsbefehlen auf dem Server.
    """

    user = models.ForeignKey(
        User, on_delete=models.PROTECT, related_name="account_events", verbose_name="Konto"
    )
    kind = models.CharField("Vorgang", max_length=20, choices=AccountEventKind.choices)
    old_role = models.CharField("Rolle vorher", max_length=20, choices=Role.choices, blank=True)
    new_role = models.CharField("Rolle nachher", max_length=20, choices=Role.choices, blank=True)
    reason = models.TextField("Begründung", blank=True)
    detail = models.TextField(
        "Angabe", blank=True, help_text="Neuer Wert, z. B. der neue Briefkopf/Stempel."
    )
    at = models.DateTimeField("Zeitpunkt", default=timezone.now)
    actor = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="+",
        null=True,
        blank=True,
        verbose_name="durch",
    )

    class Meta:
        verbose_name = "Vorgang der Kontoverwaltung"
        verbose_name_plural = "Vorgänge der Kontoverwaltung"
        ordering = ("at", "id")
        constraints = (
            models.CheckConstraint(
                condition=Q(kind__in=AccountEventKind.values), name="accounts_event_kind_valid"
            ),
            models.CheckConstraint(
                condition=~Q(kind__in=EVENTS_WITH_REASON) | Q(reason__regex=r"\S"),
                name="accounts_event_reason_required",
            ),
            models.CheckConstraint(
                condition=~Q(kind=AccountEventKind.ROLE_CHANGED)
                | (~Q(old_role="") & ~Q(new_role="") & ~Q(old_role=models.F("new_role"))),
                name="accounts_event_role_change_complete",
            ),
        )

    def __str__(self) -> str:
        return f"{self.get_kind_display()}: {self.user} am {self.at:%d.%m.%Y %H:%M}"
