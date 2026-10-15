"""Benutzer, Rollen und die Qualifikation Sachkunde (Konzept, Abschnitt 5).

Rollen bauen aufeinander auf: Admin > PSA-Sachkundig > Trainer/Materialwart > Verleiher >
Viewer. Prüfen und Signieren hängt zusätzlich an der Qualifikation Sachkunde. Sie hat ein
Ablaufdatum und wird nicht durch die Rolle ersetzt: Ein Admin ohne gültige Sachkunde darf nicht
prüfen, ein Trainer mit Sachkunde auch nicht.

Sachkunde-Einträge und Widerrufe werden nur angehängt. Eine Verlängerung ist ein neuer Eintrag,
ein Widerruf ein eigener Eintrag mit Begründung. So bleibt nachweisbar, ob die Sachkunde zu einem
bestimmten Zeitpunkt gültig war, etwa beim Signieren einer Prüfungsrunde.
"""

from datetime import datetime
from typing import Any, ClassVar

from django.contrib.auth.models import AbstractUser
from django.contrib.auth.models import UserManager as DjangoUserManager
from django.db import models
from django.db.models import Q
from django.utils import timezone

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

    @property
    def stamp(self) -> str:
        """Briefkopf/Stempel für PDFs: eigener Text oder der vollständige Name."""
        return self.stamp_text or self.get_full_name()


class Qualification(AppendOnlyModel):
    """Sachkunde einer Person für einen Zeitraum. Wird nur angehängt, nie geändert.

    Gültig vom ersten bis einschließlich zum letzten Tag, nach deutscher Zeit. Die Verknüpfung
    mit dem Signaturzertifikat folgt mit der Zertifikatsliste (#18).
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
