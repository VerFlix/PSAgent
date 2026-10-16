"""Notfallmappen und die darin gedruckten Formularnummern (Parität 22).

Beides wird nur angehängt: Welche Formularnummer in welcher Mappe gedruckt wurde, muss bei der
Nacherfassung nachweisbar bleiben, damit ein fehlendes Blatt auffällt.
"""

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone

from core.append_only import AppendOnlyModel


class FormKind(models.TextChoices):
    """Papierformulare; der Wert ist zugleich das Kürzel vor der Nummer."""

    LOAN = "A", "Ausgabe und Rückgabe"
    LOCK = "S", "Sperrung"
    INSPECTION = "P", "Prüfprotokoll"


class Binder(AppendOnlyModel):
    """Eine erzeugte Notfallmappe. Die PDF liegt als Datei daneben (`binder.path_of`)."""

    created_at = models.DateTimeField("erzeugt am", default=timezone.now)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="+",
        null=True,
        blank=True,
        verbose_name="erzeugt von",
        help_text="Leer: Verwaltungsbefehl (wöchentlicher Timer).",
    )
    sha256 = models.CharField("SHA-256 der PDF", max_length=64)

    class Meta:
        verbose_name = "Notfallmappe"
        verbose_name_plural = "Notfallmappen"
        ordering = ("-created_at", "-id")
        constraints = (
            models.CheckConstraint(
                condition=Q(sha256__regex=r"^[0-9a-f]{64}$"), name="emergency_binder_sha256"
            ),
        )

    def __str__(self) -> str:
        return f"Notfallmappe vom {timezone.localtime(self.created_at):%d.%m.%Y %H:%M}"


class FormSheet(AppendOnlyModel):
    """Ein leeres Formular mit seiner Nummer, gedruckt in genau einer Mappe."""

    binder = models.ForeignKey(
        Binder, on_delete=models.PROTECT, related_name="sheets", verbose_name="Notfallmappe"
    )
    kind = models.CharField("Formular", max_length=1, choices=FormKind.choices)
    number = models.PositiveIntegerField("Nummer")

    class Meta:
        verbose_name = "Formularnummer"
        verbose_name_plural = "Formularnummern"
        ordering = ("kind", "number")
        constraints = (
            models.UniqueConstraint(fields=["kind", "number"], name="emergency_sheet_unique"),
            models.CheckConstraint(
                condition=Q(kind__in=FormKind.values), name="emergency_sheet_kind_valid"
            ),
            models.CheckConstraint(condition=Q(number__gte=1), name="emergency_sheet_number"),
        )

    def __str__(self) -> str:
        return self.label

    @property
    def label(self) -> str:
        """Aufgedruckte Nummer, z. B. „A-00012“."""
        return f"{self.kind}-{self.number:05d}"
