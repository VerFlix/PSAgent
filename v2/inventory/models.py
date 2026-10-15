"""Stammdaten: Teile, Bauteile, GAL-Dokumente und Vorgänge am Teil (Konzept, Abschnitt 4).

Ein Teil ist ein Produkt (genau ein Bauteil) oder ein System (beliebig viele Bauteile). Die EI
steht am Bauteil und ist über alle Bauteile eindeutig, ohne Groß-/Kleinschreibung und
Leerzeichen; das erzwingt ein Unique-Index auf einer berechneten Spalte.

Der Zustand eines Teils wird nicht überschrieben, sondern aus Vorgängen abgeleitet, die nur
angehängt werden: freigegeben, „keine PSA“ gesetzt/aufgehoben, gesperrt, entsperrt,
ausgesondert. „Nächste Prüfung“ ergibt sich aus der letzten signierten Prüfung (#8).
"""

import re
from datetime import datetime

from django.db import models
from django.db.models import F, Q
from django.db.models.functions import Lower
from django.utils import timezone

from accounts.models import User
from core.append_only import AppendOnlyModel


class RegexpReplace(models.Func):
    """PostgreSQL regexp_replace(text, pattern, replacement, flags)."""

    function = "REGEXP_REPLACE"
    output_field = models.CharField()


def normalize_ei(value: str) -> str:
    """Vergleichsschlüssel einer EI: ohne Leerzeichen, klein geschrieben.

    Entspricht der berechneten Spalte `Component.ei_key` in der Datenbank.
    """
    return re.sub(r"\s", "", value).lower()


class PartKind(models.TextChoices):
    PRODUCT = "product", "Produkt"
    SYSTEM = "system", "System"


class PartStatus(models.TextChoices):
    """Abgeleiteter Status, in der Reihenfolge seines Vorrangs."""

    RETIRED = "retired", "ausgesondert"
    LOCKED = "locked", "gesperrt"
    NEW = "new", "neu, nicht freigegeben"
    RELEASED = "released", "freigegeben"


class EventKind(models.TextChoices):
    RELEASED = "released", "freigegeben"
    NO_PPE_SET = "no_ppe_set", "„keine PSA“ gesetzt"
    NO_PPE_CLEARED = "no_ppe_cleared", "„keine PSA“ aufgehoben"
    LOCKED = "locked", "gesperrt"
    UNLOCKED = "unlocked", "entsperrt"
    RETIRED = "retired", "ausgesondert"


# Vorgänge, die einen Kommentar brauchen (Sperren, Entsperren, Aussondern).
EVENTS_WITH_COMMENT = (EventKind.LOCKED, EventKind.UNLOCKED, EventKind.RETIRED)


class Document(models.Model):
    """GAL des Herstellers: Datei (einmal gespeichert, per SHA-256 erkannt) oder Link."""

    title = models.CharField("Titel", max_length=200)
    file = models.FileField("Datei", upload_to="gal/", blank=True)
    sha256 = models.CharField("SHA-256 der Datei", max_length=64, blank=True)
    url = models.URLField("Link", max_length=500, blank=True)
    created_at = models.DateTimeField("angelegt am", default=timezone.now)
    created_by = models.ForeignKey(
        User, on_delete=models.PROTECT, related_name="+", null=True, blank=True
    )

    class Meta:
        verbose_name = "Dokument (GAL)"
        verbose_name_plural = "Dokumente (GAL)"
        constraints = (
            models.CheckConstraint(
                condition=(~Q(file="") & Q(sha256__regex=r"^[0-9a-f]{64}$") & Q(url=""))
                | (Q(file="") & Q(sha256="") & ~Q(url="")),
                name="inventory_document_file_xor_url",
            ),
            models.UniqueConstraint(
                fields=["sha256"],
                condition=~Q(sha256=""),
                name="inventory_document_sha256_unique",
            ),
            models.CheckConstraint(
                condition=Q(url="") | Q(url__regex=r"^https?://"),
                name="inventory_document_url_http",
            ),
            models.CheckConstraint(
                condition=Q(file="") | Q(file__endswith=".pdf"),
                name="inventory_document_file_pdf",
            ),
        )

    def __str__(self) -> str:
        return self.title


class Part(models.Model):
    """Ein Teil: Produkt oder System. Wird nie gelöscht, sondern ausgesondert."""

    kind = models.CharField("Art", max_length=10, choices=PartKind.choices)
    name = models.CharField("Bezeichnung", max_length=200)
    documents = models.ManyToManyField(
        Document, blank=True, related_name="parts", verbose_name="GAL"
    )
    legacy_kind = models.CharField(
        "Art im Altsystem", max_length=10, blank=True, choices=PartKind.choices
    )
    legacy_id = models.PositiveIntegerField("ID im Altsystem", null=True, blank=True)
    created_at = models.DateTimeField("angelegt am", default=timezone.now)
    created_by = models.ForeignKey(
        User, on_delete=models.PROTECT, related_name="+", null=True, blank=True
    )

    class Meta:
        verbose_name = "Teil"
        verbose_name_plural = "Teile"
        constraints = (
            models.CheckConstraint(
                condition=Q(kind__in=PartKind.values), name="inventory_part_kind_valid"
            ),
            models.CheckConstraint(
                condition=(Q(legacy_id__isnull=True) & Q(legacy_kind=""))
                | (Q(legacy_id__isnull=False) & Q(legacy_kind__in=PartKind.values)),
                name="inventory_part_legacy_complete",
            ),
            models.UniqueConstraint(
                fields=["legacy_kind", "legacy_id"],
                condition=Q(legacy_id__isnull=False),
                name="inventory_part_legacy_unique",
            ),
        )

    def __str__(self) -> str:
        return self.name

    def _events(self, at: datetime | None) -> list["PartEvent"]:
        events = self.events.all()
        if at is not None:
            events = events.filter(at__lte=at)
        return list(events)

    def is_retired(self, at: datetime | None = None) -> bool:
        return any(e.kind == EventKind.RETIRED for e in self._events(at))

    def is_locked(self, at: datetime | None = None) -> bool:
        """Gesperrt, wenn der letzte Sperr-/Entsperr-Vorgang eine Sperre war."""
        relevant = [e for e in self._events(at) if e.kind in (EventKind.LOCKED, EventKind.UNLOCKED)]
        return bool(relevant) and relevant[-1].kind == EventKind.LOCKED

    def is_released(self, at: datetime | None = None) -> bool:
        """Freigegeben durch die erste bestandene Prüfung oder durch „keine PSA“."""
        return any(e.kind in (EventKind.RELEASED, EventKind.NO_PPE_SET) for e in self._events(at))

    def has_no_ppe(self, at: datetime | None = None) -> bool:
        relevant = [
            e
            for e in self._events(at)
            if e.kind in (EventKind.NO_PPE_SET, EventKind.NO_PPE_CLEARED)
        ]
        return bool(relevant) and relevant[-1].kind == EventKind.NO_PPE_SET

    def status(self, at: datetime | None = None) -> PartStatus:
        """Status zum Zeitpunkt `at` (Standard: jetzt): ausgesondert > gesperrt > neu > frei."""
        if self.is_retired(at):
            return PartStatus.RETIRED
        if self.is_locked(at):
            return PartStatus.LOCKED
        if not self.is_released(at):
            return PartStatus.NEW
        return PartStatus.RELEASED


class Component(models.Model):
    """Bauteil mit den 9 Feldern des Altsystems. Ein Produkt hat genau eines."""

    part = models.ForeignKey(
        Part, on_delete=models.PROTECT, related_name="components", verbose_name="Teil"
    )
    position = models.PositiveSmallIntegerField("Position")
    designation = models.CharField("Produktbezeichnung", max_length=200, blank=True)
    standard = models.CharField("gem. EN", max_length=100, blank=True)
    model_name = models.CharField("Produktname", max_length=200, blank=True)
    manufacturer = models.CharField("Hersteller", max_length=200, blank=True)
    year_of_manufacture = models.PositiveSmallIntegerField(
        "Herstellungsjahr", null=True, blank=True
    )
    purchase_date = models.DateField("Kaufdatum", null=True, blank=True)
    first_use_date = models.DateField("Datum 1. Einsatz", null=True, blank=True)
    ei = models.CharField("Einzelidentifikation (EI)", max_length=100)
    serial_number = models.CharField("Seriennummer", max_length=100, blank=True)
    ei_key = models.GeneratedField(
        expression=Lower(
            RegexpReplace(F("ei"), models.Value(r"\s"), models.Value(""), models.Value("g"))
        ),
        output_field=models.CharField(max_length=100),
        db_persist=True,
        verbose_name="EI-Vergleichsschlüssel",
    )

    class Meta:
        verbose_name = "Bauteil"
        verbose_name_plural = "Bauteile"
        ordering = ("part", "position")
        constraints = (
            models.UniqueConstraint(fields=["ei_key"], name="inventory_component_ei_unique"),
            models.CheckConstraint(
                condition=Q(ei__regex=r"\S"), name="inventory_component_ei_required"
            ),
            models.UniqueConstraint(
                fields=["part", "position"], name="inventory_component_position_unique"
            ),
            models.CheckConstraint(
                condition=Q(position__gte=1), name="inventory_component_position_positive"
            ),
            models.CheckConstraint(
                condition=Q(year_of_manufacture__isnull=True)
                | Q(year_of_manufacture__gte=1900, year_of_manufacture__lte=2200),
                name="inventory_component_year_plausible",
            ),
        )

    def __str__(self) -> str:
        return f"{self.ei} ({self.designation or self.model_name or 'Bauteil'})"


class PartEvent(AppendOnlyModel):
    """Vorgang am Teil. Wird nur angehängt; daraus ergibt sich der Status."""

    part = models.ForeignKey(
        Part, on_delete=models.PROTECT, related_name="events", verbose_name="Teil"
    )
    kind = models.CharField("Vorgang", max_length=20, choices=EventKind.choices)
    at = models.DateTimeField("Zeitpunkt", default=timezone.now)
    actor = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="+",
        null=True,
        blank=True,
        verbose_name="durch",
    )
    comment = models.TextField("Kommentar", blank=True)

    class Meta:
        verbose_name = "Vorgang am Teil"
        verbose_name_plural = "Vorgänge an Teilen"
        ordering = ("at", "id")
        constraints = (
            models.CheckConstraint(
                condition=Q(kind__in=EventKind.values), name="inventory_event_kind_valid"
            ),
            models.CheckConstraint(
                condition=~Q(kind__in=EVENTS_WITH_COMMENT) | Q(comment__regex=r"\S"),
                name="inventory_event_comment_required",
            ),
            models.UniqueConstraint(
                fields=["part"],
                condition=Q(kind=EventKind.RELEASED),
                name="inventory_event_released_once",
            ),
            models.UniqueConstraint(
                fields=["part"],
                condition=Q(kind=EventKind.RETIRED),
                name="inventory_event_retired_once",
            ),
        )

    def __str__(self) -> str:
        return f"{self.get_kind_display()}: {self.part} ({self.at:%d.%m.%Y %H:%M})"
