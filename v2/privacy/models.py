"""Datenschutz: Löschsperren und Löschprotokoll (#63). Beides wird nur angehängt.

Beide Tabellen nennen den Entleiher nur über seine Nummer; Namen stehen hier nie.
"""

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone

from core.append_only import AppendOnlyModel


class ErasureHold(AppendOnlyModel):
    """Löschsperre setzen (`active`) oder aufheben; es gilt der jüngste Eintrag.

    Für einen konkreten Rechtsstreit oder Schadensfall (Art. 17 Abs. 3 lit. e, Art. 18 DSGVO).
    """

    borrower = models.ForeignKey(
        "lending.Borrower",
        on_delete=models.PROTECT,
        related_name="erasure_holds",
        verbose_name="Entleiher",
    )
    active = models.BooleanField("Löschsperre gilt")
    reason = models.TextField("Grund")
    at = models.DateTimeField("Zeitpunkt", default=timezone.now)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="+",
        null=True,
        blank=True,
        verbose_name="durch",
    )

    class Meta:
        verbose_name = "Löschsperre"
        verbose_name_plural = "Löschsperren"
        ordering = ("-at", "-id")
        constraints = (
            models.CheckConstraint(condition=Q(reason__regex=r"\S"), name="privacy_hold_reason"),
        )

    @classmethod
    def current(cls, borrower_id: int) -> "ErasureHold | None":
        """Die geltende Löschsperre des Entleihers, sonst `None`."""
        latest = cls.objects.filter(borrower_id=borrower_id).first()
        return latest if latest is not None and latest.active else None

    def __str__(self) -> str:
        state = "gesetzt" if self.active else "aufgehoben"
        return f"Löschsperre Entleiher #{self.borrower_id} {state}"


class Erasure(AppendOnlyModel):
    """Protokoll: Personendaten eines Entleihers wurden gelöscht (Rechenschaft, Art. 5 Abs. 2)."""

    borrower = models.OneToOneField(
        "lending.Borrower",
        on_delete=models.PROTECT,
        related_name="erasure",
        verbose_name="Entleiher",
    )
    at = models.DateTimeField("gelöscht am", default=timezone.now)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="+",
        null=True,
        blank=True,
        verbose_name="durch",
        help_text="Leer: täglicher Lauf oder Verwaltungsbefehl.",
    )
    by_deadline = models.BooleanField("nach Ablauf der Löschfrist")
    last_activity = models.DateField("letzte Aktivität")

    class Meta:
        verbose_name = "Löschung"
        verbose_name_plural = "Löschungen"
        ordering = ("-at", "-id")

    def __str__(self) -> str:
        return f"Entleiher #{self.borrower_id} gelöscht am {self.at:%d.%m.%Y}"
