"""Einstellungen der Theke (#127, Teil 3)."""

from django.db import models
from django.utils import timezone

from accounts.models import User
from core.append_only import AppendOnlyModel


class CounterSetting(AppendOnlyModel):
    """Art-Kürzel der Theke und Vorauswahl der Leihdauer; es gilt der jüngste Eintrag.

    Festlegung (Felix, 03.11.2026): nur Admin, jede Änderung mit dem YubiKey bestätigt. Ohne
    Eintrag gelten die Startwerte aus `counter.config`. Personendaten stehen hier keine.
    """

    kinds = models.JSONField(
        "Art-Kürzel", help_text="Liste aus Kürzel, Name, Schnellzugriff, nach Größe"
    )
    default_days = models.PositiveSmallIntegerField("Vorauswahl der Leihdauer (Tage)")
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
        verbose_name = "Einstellung der Theke"
        verbose_name_plural = "Einstellungen der Theke"
        ordering = ("-at", "-id")

    def __str__(self) -> str:
        return f"Theke: {len(self.kinds)} Art-Kürzel, Vorauswahl {self.default_days} Tage"
