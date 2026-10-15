"""PSA-Prüfung: Prüfungsrunden, Prüfergebnisse je Teil und Vorgänge der Runde.

Alle drei Tabellen werden nur angehängt (CLAUDE.md, Regel 3), auch in der Datenbank erzwungen.

- **Prüfungsrunde:** sammelt Prüfergebnisse eines Sachkundigen, die gemeinsam abgeschlossen und
  signiert werden (Parität 7).
- **Prüfergebnis:** je Teil Ergebnis, Kommentar und nächste Prüfung. Eine Korrektur ist ein
  neues Ergebnis mit Verweis auf das alte; jedes Ergebnis wird höchstens einmal korrigiert.
- **Vorgang der Runde:** abgeschlossen (Inhalt eingefroren, mit SHA-256 des Inhalts), signiert,
  abgebrochen oder als Altbestand übernommen. Der Zustand einer Runde ergibt sich daraus.

Erst mit der Signatur (oder der Übernahme aus dem Altsystem) wirken die Ergebnisse auf die
Teile: Freigabe, Sperre, Entsperrung, Aussonderung und „nächste Prüfung“. Bis dahin ändert eine
Runde nichts am Bestand.
"""

from django.db import models
from django.db.models import Q
from django.utils import timezone

from accounts.models import User
from core.append_only import AppendOnlyModel
from inventory.models import Part


class Outcome(models.TextChoices):
    PASSED = "passed", "in Ordnung"
    DEFECT = "defect", "Mangel, sperren"
    RETIRE = "retire", "aussondern"


# Ergebnisse, die einen Kommentar brauchen.
OUTCOMES_WITH_COMMENT = (Outcome.DEFECT, Outcome.RETIRE)


class RoundEventKind(models.TextChoices):
    CLOSED = "closed", "abgeschlossen"
    SIGNED = "signed", "signiert"
    CANCELLED = "cancelled", "abgebrochen"
    LEGACY = "legacy", "Altbestand übernommen"


# Vorgänge, mit denen die Ergebnisse einer Runde auf die Teile wirken.
EFFECTIVE_KINDS = (RoundEventKind.SIGNED, RoundEventKind.LEGACY)


class RoundState(models.TextChoices):
    """Abgeleiteter Zustand einer Runde."""

    OPEN = "open", "offen"
    CLOSED = "closed", "abgeschlossen, nicht signiert"
    SIGNED = "signed", "signiert"
    LEGACY = "legacy", "Altbestand übernommen"
    CANCELLED = "cancelled", "abgebrochen"


class InspectionRound(AppendOnlyModel):
    """Prüfungsrunde eines Sachkundigen. Ohne Konto nur bei Übernahme aus dem Altsystem."""

    inspector = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="inspection_rounds",
        null=True,
        blank=True,
        verbose_name="Sachkundige Person",
    )
    inspector_name = models.CharField(
        "Name der sachkundigen Person",
        max_length=200,
        blank=True,
        help_text="Nur bei Übernahme aus dem Altsystem, wo es kein Konto gibt.",
    )
    started_at = models.DateTimeField("begonnen am", default=timezone.now)
    legacy_report_id = models.PositiveIntegerField(
        "Prüfbericht im Altsystem", null=True, blank=True, unique=True
    )

    class Meta:
        verbose_name = "Prüfungsrunde"
        verbose_name_plural = "Prüfungsrunden"
        ordering = ("started_at", "id")
        constraints = (
            models.CheckConstraint(
                condition=Q(inspector__isnull=False) | Q(inspector_name__regex=r"\S"),
                name="inspections_round_inspector_known",
            ),
        )

    def __str__(self) -> str:
        return f"Prüfungsrunde #{self.pk} vom {timezone.localtime(self.started_at):%d.%m.%Y}"

    @property
    def inspector_display(self) -> str:
        if self.inspector is not None:
            return self.inspector.get_full_name() or self.inspector.get_username()
        return self.inspector_name

    def state(self) -> RoundState:
        kinds = set(self.events.values_list("kind", flat=True))
        for kind in (
            RoundEventKind.CANCELLED,
            RoundEventKind.SIGNED,
            RoundEventKind.LEGACY,
            RoundEventKind.CLOSED,
        ):
            if kind in kinds:
                return RoundState(kind.value)
        return RoundState.OPEN

    def current_results(self) -> "models.QuerySet[InspectionResult]":
        """Ergebnisse der Runde ohne die, die in derselben Runde korrigiert wurden."""
        return (
            self.results.exclude(correction__round=self)
            .select_related("part", "corrects")
            .order_by("part_id", "id")
        )


class InspectionResult(AppendOnlyModel):
    """Prüfergebnis für ein Teil in einer Runde. Korrekturen verweisen auf das alte Ergebnis."""

    round = models.ForeignKey(
        InspectionRound,
        on_delete=models.PROTECT,
        related_name="results",
        verbose_name="Prüfungsrunde",
    )
    part = models.ForeignKey(
        Part, on_delete=models.PROTECT, related_name="inspection_results", verbose_name="Teil"
    )
    outcome = models.CharField("Ergebnis", max_length=10, choices=Outcome.choices)
    comment = models.TextField("Kommentar", blank=True)
    next_due = models.DateField("nächste Prüfung", null=True, blank=True)
    corrects = models.OneToOneField(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="correction",
        verbose_name="korrigiert",
    )
    recorded_at = models.DateTimeField("erfasst am", default=timezone.now)
    recorded_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="+",
        null=True,
        blank=True,
        verbose_name="erfasst von",
    )

    class Meta:
        verbose_name = "Prüfergebnis"
        verbose_name_plural = "Prüfergebnisse"
        ordering = ("recorded_at", "id")
        constraints = (
            models.CheckConstraint(
                condition=Q(outcome__in=Outcome.values),
                name="inspections_result_outcome_valid",
            ),
            models.CheckConstraint(
                condition=~Q(outcome__in=OUTCOMES_WITH_COMMENT) | Q(comment__regex=r"\S"),
                name="inspections_result_comment_required",
            ),
            models.CheckConstraint(
                condition=~Q(outcome=Outcome.PASSED) | Q(next_due__isnull=False),
                name="inspections_result_passed_needs_next_due",
            ),
            models.CheckConstraint(
                condition=~Q(outcome=Outcome.RETIRE) | Q(next_due__isnull=True),
                name="inspections_result_retire_without_next_due",
            ),
            models.CheckConstraint(
                condition=~Q(corrects=models.F("id")),
                name="inspections_result_not_self_correcting",
            ),
            models.UniqueConstraint(
                fields=["round", "part"],
                condition=Q(corrects__isnull=True),
                name="inspections_result_once_per_round",
            ),
        )

    def __str__(self) -> str:
        return f"{self.part}: {self.get_outcome_display()}"


class RoundEvent(AppendOnlyModel):
    """Vorgang einer Prüfungsrunde. Jede Art höchstens einmal je Runde."""

    round = models.ForeignKey(
        InspectionRound,
        on_delete=models.PROTECT,
        related_name="events",
        verbose_name="Prüfungsrunde",
    )
    kind = models.CharField("Vorgang", max_length=10, choices=RoundEventKind.choices)
    at = models.DateTimeField("Zeitpunkt", default=timezone.now)
    actor = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="+",
        null=True,
        blank=True,
        verbose_name="durch",
    )
    content_sha256 = models.CharField(
        "SHA-256 des Inhalts",
        max_length=64,
        blank=True,
        help_text="Beim Abschluss: Prüfsumme des eingefrorenen Inhalts der Runde.",
    )
    comment = models.TextField("Kommentar", blank=True)

    class Meta:
        verbose_name = "Vorgang der Prüfungsrunde"
        verbose_name_plural = "Vorgänge der Prüfungsrunden"
        ordering = ("at", "id")
        constraints = (
            models.CheckConstraint(
                condition=Q(kind__in=RoundEventKind.values),
                name="inspections_event_kind_valid",
            ),
            models.UniqueConstraint(
                fields=["round", "kind"], name="inspections_event_once_per_round"
            ),
            models.CheckConstraint(
                condition=(
                    Q(kind=RoundEventKind.CLOSED) & Q(content_sha256__regex=r"^[0-9a-f]{64}$")
                )
                | (~Q(kind=RoundEventKind.CLOSED) & Q(content_sha256="")),
                name="inspections_event_hash_on_close",
            ),
            models.CheckConstraint(
                condition=~Q(kind=RoundEventKind.CANCELLED) | Q(comment__regex=r"\S"),
                name="inspections_event_cancel_comment_required",
            ),
        )

    def __str__(self) -> str:
        return f"{self.round}: {self.get_kind_display()}"
