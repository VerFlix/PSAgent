"""Kettenglieder der Hash-Kette (#15); Logik in `core/chain.py`."""

from django.db import models
from django.db.models import Q
from django.utils import timezone

from core.append_only import AppendOnlyModel


class ChainLink(AppendOnlyModel):
    """Ein Glied der Hash-Kette: Prüfsumme eines Vorgangs und Hash des Vorgängers. Nur anhängen."""

    chained = False  # das Glied selbst wird nicht noch einmal verkettet

    seq = models.PositiveBigIntegerField("Nummer", unique=True)
    prev_hash = models.CharField("Hash des Vorgängers", max_length=64)
    table = models.CharField("Tabelle", max_length=100)
    row_pk = models.CharField("Schlüssel der Zeile", max_length=64)
    fields = models.JSONField("Spalten")
    payload_sha256 = models.CharField("SHA-256 der Zeile", max_length=64)
    hash = models.CharField("Hash", max_length=64, unique=True)
    at = models.DateTimeField("Zeitpunkt", default=timezone.now)

    class Meta:
        verbose_name = "Kettenglied"
        verbose_name_plural = "Kettenglieder"
        ordering = ("seq",)
        constraints = (
            models.UniqueConstraint(fields=["table", "row_pk"], name="core_chain_row_once"),
            models.CheckConstraint(
                condition=Q(hash__regex=r"^[0-9a-f]{64}$")
                & Q(prev_hash__regex=r"^[0-9a-f]{64}$")
                & Q(payload_sha256__regex=r"^[0-9a-f]{64}$"),
                name="core_chain_sha256",
            ),
        )

    def __str__(self) -> str:
        return f"Glied {self.seq}: {self.table} #{self.row_pk}"
