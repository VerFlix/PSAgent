"""Prüfberichte aus dem Altsystem, wörtlich übernommen (Paritätszeile 9, #10).

Die Altberichte (`psa_pruefungsberichte`, `psa_pruefungsbericht_items`) werden Feld für Feld
als Text übernommen und nur angehängt. Sie haben keine Wirkung auf die Teile: Den Stand am
Stichtag (nächste Prüfung, Sperren, Aussonderung) übernimmt der Import getrennt. Die
Unterschrift des Altberichts bleibt die gezeichnete Strichliste und ist „Altbestand,
ungesichert“.
"""

from django.db import models
from django.utils import timezone

from core.append_only import AppendOnlyModel
from inventory.models import Part


class LegacyReport(AppendOnlyModel):
    """Kopf eines Prüfberichts aus dem Altsystem."""

    legacy_id = models.PositiveIntegerField("Prüfbericht im Altsystem", unique=True)
    created_at_raw = models.TextField("erstellt am (wie im Altsystem)", blank=True)
    created_at = models.DateTimeField("erstellt am", null=True, blank=True)
    inspector_name = models.TextField("Name der sachkundigen Person", blank=True)
    confirmed = models.BooleanField("„Ich bin PSA-Sachkundig“ bestätigt", default=False)
    signature_data = models.TextField("Unterschrift (Strichliste, ungesichert)", blank=True)
    imported_at = models.DateTimeField("übernommen am", default=timezone.now)

    class Meta:
        verbose_name = "Prüfbericht aus dem Altsystem"
        verbose_name_plural = "Prüfberichte aus dem Altsystem"
        ordering = ("legacy_id",)

    def __str__(self) -> str:
        return f"Altbericht #{self.legacy_id}"


class LegacyReportItem(AppendOnlyModel):
    """Eintrag eines Altberichts; verweist auf das übernommene Teil, wenn es eines gibt."""

    report = models.ForeignKey(
        LegacyReport, on_delete=models.PROTECT, related_name="items", verbose_name="Altbericht"
    )
    legacy_id = models.PositiveIntegerField("Eintrag im Altsystem", unique=True)
    item_type = models.TextField("Art im Altsystem", blank=True)
    item_id = models.IntegerField("ID im Altsystem", null=True, blank=True)
    item_label = models.TextField("Beschriftung", blank=True)
    inspection_done = models.BooleanField("PSA-Prüfung durchgeführt", default=False)
    comment = models.TextField("Kommentar", blank=True)
    next_due_raw = models.TextField("nächste Prüfung (wie im Altsystem)", blank=True)
    action = models.TextField("Aktion", blank=True)
    part = models.ForeignKey(
        Part,
        on_delete=models.PROTECT,
        related_name="legacy_report_items",
        null=True,
        blank=True,
        verbose_name="Teil",
    )

    class Meta:
        verbose_name = "Eintrag eines Altberichts"
        verbose_name_plural = "Einträge von Altberichten"
        ordering = ("report", "legacy_id")

    def __str__(self) -> str:
        return f"{self.report}: {self.item_label}"
