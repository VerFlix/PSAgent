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


class PaperForm(AppendOnlyModel):
    """Ein ausgefülltes Papierformular, nacherfasst mit Scan (Konzept Abschnitt 7).

    Jede Formularnummer wird genau einmal erfasst. Maßgeblich bleibt das Papieroriginal; der
    Scan liegt als PDF mit Prüfsumme daneben (`backfill.scan_path`).
    """

    sheet = models.OneToOneField(
        FormSheet, on_delete=models.PROTECT, related_name="paper_form", verbose_name="Formular"
    )
    scan_sha256 = models.CharField("SHA-256 des Scans", max_length=64)
    recorded_at = models.DateTimeField("nachgetragen am", default=timezone.now)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="+",
        null=True,
        blank=True,
        verbose_name="nachgetragen von",
    )

    class Meta:
        verbose_name = "nacherfasstes Formular"
        verbose_name_plural = "nacherfasste Formulare"
        ordering = ("-recorded_at", "-id")
        constraints = (
            models.CheckConstraint(
                condition=Q(scan_sha256__regex=r"^[0-9a-f]{64}$"), name="emergency_form_sha256"
            ),
        )

    def __str__(self) -> str:
        return f"Formular {self.sheet.label}"


class LockEntry(AppendOnlyModel):
    """Eine Zeile eines Sperrformulars. Ohne Vorgang am Teil, wenn sie nicht übernommen wurde."""

    paper_form = models.ForeignKey(
        PaperForm, on_delete=models.PROTECT, related_name="lock_entries", verbose_name="Formular"
    )
    part = models.ForeignKey(
        "inventory.Part", on_delete=models.PROTECT, related_name="+", verbose_name="Teil"
    )
    reason = models.TextField("Grund")
    locked_on = models.DateField("gesperrt am (laut Formular)")
    locked_by_name = models.CharField("gesperrt von (laut Formular)", max_length=200)
    part_event = models.OneToOneField(
        "inventory.PartEvent",
        on_delete=models.PROTECT,
        related_name="+",
        null=True,
        blank=True,
        verbose_name="Vorgang am Teil",
    )
    note = models.TextField("Hinweis", blank=True, help_text="Warum nicht übernommen.")

    class Meta:
        verbose_name = "nacherfasste Sperrung"
        verbose_name_plural = "nacherfasste Sperrungen"
        ordering = ("paper_form", "id")
        constraints = (
            models.CheckConstraint(
                condition=Q(part_event__isnull=False) | Q(note__regex=r"\S"),
                name="emergency_lock_entry_note_if_not_applied",
            ),
        )

    def __str__(self) -> str:
        return f"Sperrung {self.part} aus {self.paper_form}"


class Clarification(AppendOnlyModel):
    """Ein geklärter Fall der Klärungsliste; der Schlüssel benennt den Fall (`backfill.Case`)."""

    key = models.CharField("Fall", max_length=100, unique=True)
    note = models.TextField("Klärung")
    resolved_at = models.DateTimeField("geklärt am", default=timezone.now)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="+",
        null=True,
        blank=True,
        verbose_name="geklärt von",
    )

    class Meta:
        verbose_name = "Klärung"
        verbose_name_plural = "Klärungen"
        ordering = ("-resolved_at", "-id")
        constraints = (
            models.CheckConstraint(condition=Q(note__regex=r"\S"), name="emergency_note_required"),
        )

    def __str__(self) -> str:
        return f"{self.key}: {self.note}"


class LoanForm(AppendOnlyModel):
    """Formular „Ausgabe und Rückgabe“, wie es auf dem Papier steht (Konzept Abschnitt 7).

    Kontaktdaten stehen nicht hier, sondern beim Entleiher. Ob und wie das Formular in den
    Verleih übernommen wurde, steht in `LoanFormResult`; fehlt es, wartet eine Doppelbuchung
    auf die Klärungsliste.
    """

    paper_form = models.OneToOneField(
        PaperForm, on_delete=models.PROTECT, related_name="loan_form", verbose_name="Formular"
    )
    borrower = models.ForeignKey(
        "lending.Borrower",
        on_delete=models.PROTECT,
        related_name="+",
        null=True,
        blank=True,
        verbose_name="Entleiher",
    )
    quick_check = models.BooleanField("Kurzkontrolle angekreuzt", default=False)
    gal_provided = models.BooleanField("GAL bereitgestellt angekreuzt", default=False)
    issued_on = models.DateField("Datum Ausgabe", null=True, blank=True)
    planned_return = models.DateField("Rückgabe geplant am", null=True, blank=True)
    issued_by_name = models.CharField("ausgebende Person", max_length=200, blank=True)
    returned_on = models.DateField("Datum Rückgabe", null=True, blank=True)
    return_quick_check = models.BooleanField("Kurzkontrolle bei Rückgabe", default=False)
    return_comment = models.TextField("Rückgabe-Kommentar", blank=True)
    received_by_name = models.CharField("rücknehmende Person", max_length=200, blank=True)
    lock_reason = models.TextField("Sperrung bei Rückgabe, Grund", blank=True)
    warnings = models.TextField(
        "zu prüfen", blank=True, help_text="Regelverstöße laut Stand, je Zeile einer."
    )

    class Meta:
        verbose_name = "nacherfasste Ausgabe/Rückgabe"
        verbose_name_plural = "nacherfasste Ausgaben/Rückgaben"
        ordering = ("paper_form", "id")
        constraints = (
            models.CheckConstraint(
                condition=Q(issued_on__isnull=False) | Q(returned_on__isnull=False),
                name="emergency_loan_form_has_content",
            ),
        )

    def __str__(self) -> str:
        return f"Ausgabe/Rückgabe aus {self.paper_form}"


class LoanFormPart(AppendOnlyModel):
    """Ein Teil (EI) auf dem Formular „Ausgabe und Rückgabe“."""

    loan_form = models.ForeignKey(
        LoanForm, on_delete=models.PROTECT, related_name="parts", verbose_name="Formular"
    )
    part = models.ForeignKey(
        "inventory.Part", on_delete=models.PROTECT, related_name="+", verbose_name="Teil"
    )

    class Meta:
        verbose_name = "Teil auf dem Formular"
        verbose_name_plural = "Teile auf dem Formular"
        ordering = ("loan_form", "id")
        constraints = (
            models.UniqueConstraint(fields=["loan_form", "part"], name="emergency_form_part_once"),
        )


class LoanFormLoan(AppendOnlyModel):
    """Ausleihe, die ein Formular ausgegeben oder zurückgenommen hat.

    Verbindet auch eine reine Rückgabe mit dem Entleiher, damit der Scan (mit seiner
    Unterschrift) beim Löschen des Entleihers gefunden wird (#63).
    """

    loan_form = models.ForeignKey(
        LoanForm, on_delete=models.PROTECT, related_name="loans", verbose_name="Formular"
    )
    loan = models.ForeignKey(
        "lending.Loan", on_delete=models.PROTECT, related_name="+", verbose_name="Ausleihe"
    )

    class Meta:
        verbose_name = "Ausleihe aus dem Formular"
        verbose_name_plural = "Ausleihen aus dem Formular"
        constraints = (
            models.UniqueConstraint(fields=["loan_form", "loan"], name="emergency_form_loan_once"),
        )

    def __str__(self) -> str:
        return f"{self.loan_form} – {self.loan}"


class LoanFormResult(AppendOnlyModel):
    """Übernahme eines Formulars in den Verleih; bei Doppelbuchung erst nach der Klärung."""

    loan_form = models.OneToOneField(
        LoanForm, on_delete=models.PROTECT, related_name="result", verbose_name="Formular"
    )
    booking = models.ForeignKey(
        "lending.Booking",
        on_delete=models.PROTECT,
        related_name="+",
        null=True,
        blank=True,
        verbose_name="Ausleihvorgang",
        help_text="Leer bei reiner Rückgabe.",
    )
    applied_at = models.DateTimeField("übernommen am", default=timezone.now)
    applied_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="+",
        null=True,
        blank=True,
        verbose_name="übernommen von",
    )

    class Meta:
        verbose_name = "Übernahme in den Verleih"
        verbose_name_plural = "Übernahmen in den Verleih"
