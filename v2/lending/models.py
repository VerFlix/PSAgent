"""Verleih: Entleiher, Ausleihvorgänge, Ausleihen je Teil, Vorgänge und Unterschriften.

Eine Ausleihe (`Loan`) gilt für genau ein Teil und einen Zeitraum [von, bis], beide Tage
eingeschlossen. Mehrere Teile eines Entleihers gehören zu einem Ausleihvorgang (`Booking`).

- **Doppelbuchungen schließt die Datenbank aus:** Eine Ausschlussregel (btree_gist) verbietet
  überlappende reservierte oder ausgeliehene Zeiträume desselben Teils, auch bei gleichzeitigen
  Zugriffen und an der Anwendung vorbei (CLAUDE.md, Fachregeln).
- **Status nur vorwärts:** reserviert → ausgeliehen → zurückgegeben, oder reserviert →
  storniert. Ein Trigger lässt an einer Ausleihe nur diesen Statuswechsel zu; alles andere ist
  unveränderlich, Löschen ist verboten.
- **Vorgänge und Unterschriften werden nur angehängt.** Sie halten fest, wer wann ausgegeben,
  zurückgenommen oder storniert hat, mit Kurzkontrolle, „GAL bereitgestellt“ und Unterschrift.
- **Kontaktdaten stehen getrennt** vom Entleiher, damit sie nach der Löschfrist gelöscht werden
  können, ohne die Historie zu zerstören (Konzept, Abschnitte 4 und 7).
"""

from datetime import date, datetime, timedelta

from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import DateRangeField, RangeOperators
from django.db import models
from django.db.models import Q
from django.utils import timezone

from accounts.models import User
from core.append_only import AppendOnlyModel
from inventory.models import Part


class Borrower(models.Model):
    """Entleiher mit dauerhafter Nummer. Personendaten stehen in `BorrowerContact`."""

    user = models.OneToOneField(
        User,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="borrower",
        verbose_name="Nutzerkonto",
        help_text="Nur wenn der Entleiher selbst ein Konto hat (Selbstausleihe).",
    )
    created_at = models.DateTimeField("angelegt am", default=timezone.now)
    created_by = models.ForeignKey(
        User, on_delete=models.PROTECT, related_name="+", null=True, blank=True
    )

    class Meta:
        verbose_name = "Entleiher"
        verbose_name_plural = "Entleiher"

    def __str__(self) -> str:
        return self.display_name

    @property
    def display_name(self) -> str:
        """Name aus den Kontaktdaten, nach dem Löschen ein neutraler Platzhalter."""
        contact = BorrowerContact.objects.filter(borrower=self).first()
        if contact is None:
            return f"Entleiher #{self.pk} (Kontaktdaten gelöscht)"
        return contact.name


class BorrowerContact(models.Model):
    """Kontaktdaten eines Entleihers. Werden nach Ablauf der Löschfrist gelöscht."""

    borrower = models.OneToOneField(
        Borrower, on_delete=models.CASCADE, related_name="contact", verbose_name="Entleiher"
    )
    name = models.CharField("Name", max_length=200)
    address = models.TextField("Adresse", blank=True)
    email = models.EmailField("E-Mail", blank=True)
    phone = models.CharField("Telefon", max_length=50, blank=True)
    updated_at = models.DateTimeField("geändert am", default=timezone.now)
    confirmed_at = models.DateTimeField(
        "zuletzt als unverändert bestätigt",
        null=True,
        blank=True,
        help_text="Bei der Ausgabe nachgefragt: Kontaktdaten unverändert.",
    )

    class Meta:
        verbose_name = "Kontaktdaten"
        verbose_name_plural = "Kontaktdaten"
        constraints = (
            models.CheckConstraint(
                condition=Q(name__regex=r"\S"), name="lending_contact_name_required"
            ),
        )

    def __str__(self) -> str:
        return self.name

    @property
    def checked_at(self) -> datetime:
        """Wann die Kontaktdaten zuletzt geändert oder als unverändert bestätigt wurden."""
        return max(self.updated_at, self.confirmed_at or self.updated_at)

    def gaps(self) -> list[str]:
        """Was für die Ausgabe fehlt; zeigt nur, *dass* etwas fehlt, nicht die Werte."""
        missing = []
        if not self.address.strip():
            missing.append("Adresse")
        if not (self.email.strip() or self.phone.strip()):
            missing.append("E-Mail oder Telefon")
        return missing


class Booking(AppendOnlyModel):
    """Ausleihvorgang: ein Entleiher, ein oder mehrere Teile. Wird nur angehängt."""

    borrower = models.ForeignKey(
        Borrower, on_delete=models.PROTECT, related_name="bookings", verbose_name="Entleiher"
    )
    created_at = models.DateTimeField("angelegt am", default=timezone.now)
    created_by = models.ForeignKey(
        User, on_delete=models.PROTECT, related_name="+", null=True, blank=True
    )
    legacy_id = models.PositiveIntegerField(
        "Ausleihe im Altsystem", null=True, blank=True, unique=True
    )

    class Meta:
        verbose_name = "Ausleihvorgang"
        verbose_name_plural = "Ausleihvorgänge"

    def __str__(self) -> str:
        return f"Ausleihvorgang #{self.pk}"


class LoanStatus(models.TextChoices):
    RESERVED = "reserved", "reserviert"
    LENT = "lent", "ausgeliehen"
    RETURNED = "returned", "zurückgegeben"
    CANCELLED = "cancelled", "storniert"


# Status, die ein Teil im Zeitraum belegen.
ACTIVE_STATUSES = (LoanStatus.RESERVED, LoanStatus.LENT)


class Loan(models.Model):
    """Ausleihe eines Teils für einen Zeitraum. Nur der Status ändert sich, und nur vorwärts."""

    booking = models.ForeignKey(
        Booking, on_delete=models.PROTECT, related_name="loans", verbose_name="Ausleihvorgang"
    )
    part = models.ForeignKey(
        Part, on_delete=models.PROTECT, related_name="loans", verbose_name="Teil"
    )
    period = DateRangeField("Zeitraum")
    status = models.CharField(
        "Status", max_length=20, choices=LoanStatus.choices, default=LoanStatus.RESERVED
    )
    created_at = models.DateTimeField("angelegt am", default=timezone.now)

    class Meta:
        verbose_name = "Ausleihe"
        verbose_name_plural = "Ausleihen"
        ordering = ("period", "id")
        constraints = (
            models.CheckConstraint(
                condition=Q(status__in=LoanStatus.values), name="lending_loan_status_valid"
            ),
            models.CheckConstraint(
                condition=Q(
                    period__isempty=False, period__lower_inf=False, period__upper_inf=False
                ),
                name="lending_loan_period_bounded",
            ),
            models.UniqueConstraint(
                fields=["booking", "part"], name="lending_loan_part_once_per_booking"
            ),
            ExclusionConstraint(
                name="lending_loan_no_overlap",
                expressions=[
                    ("part", RangeOperators.EQUAL),
                    ("period", RangeOperators.OVERLAPS),
                ],
                condition=Q(status__in=ACTIVE_STATUSES),
            ),
        )

    def __str__(self) -> str:
        return (
            f"{self.part} {self.start:%d.%m.%Y}–{self.end:%d.%m.%Y} ({self.get_status_display()})"
        )

    @property
    def start(self) -> date:
        """Erster Tag der Ausleihe."""
        value: date = self.period.lower
        return value

    @property
    def end(self) -> date:
        """Letzter Tag der Ausleihe (eingeschlossen).

        PostgreSQL speichert Datumsbereiche als [von, bis+1); daher ein Tag zurück.
        """
        upper: date = self.period.upper
        return upper - timedelta(days=1) if not self.period.upper_inc else upper

    def is_overdue(self, today: date) -> bool:
        """Ausgeliehen und der letzte Tag ist vorbei (Parität 18)."""
        return self.status == LoanStatus.LENT and self.end < today


class SignatureOrigin(models.TextChoices):
    APP = "app", "in der App erfasst"
    LEGACY = "legacy", "Altbestand, ungesichert"
    PAPER = "paper", "auf dem Papierformular (Notbetrieb)"


class Signature(AppendOnlyModel):
    """Gezeichnete Unterschrift als Strichliste [[[x, y], …], …]. Wird nur angehängt.

    Die Bindung an den Hash des Ausleihdatensatzes folgt mit der Hash-Kette (#15).
    """

    strokes = models.JSONField("Striche")
    signer_name = models.CharField("Name der unterschreibenden Person", max_length=200)
    origin = models.CharField(
        "Herkunft", max_length=10, choices=SignatureOrigin.choices, default=SignatureOrigin.APP
    )
    created_at = models.DateTimeField("erfasst am", default=timezone.now)

    class Meta:
        verbose_name = "Unterschrift"
        verbose_name_plural = "Unterschriften"
        constraints = (
            models.CheckConstraint(
                condition=Q(origin__in=SignatureOrigin.values),
                name="lending_signature_origin_valid",
            ),
        )

    def __str__(self) -> str:
        return f"Unterschrift {self.signer_name} ({self.created_at:%d.%m.%Y})"

    @property
    def is_legacy(self) -> bool:
        return self.origin == SignatureOrigin.LEGACY

    @property
    def is_paper(self) -> bool:
        return self.origin == SignatureOrigin.PAPER


class LoanEventKind(models.TextChoices):
    CHECKED_OUT = "checked_out", "ausgegeben"
    RETURNED = "returned", "zurückgenommen"
    CANCELLED = "cancelled", "storniert"


class LoanEvent(AppendOnlyModel):
    """Vorgang an einer Ausleihe. Wird nur angehängt; je Ausleihe jede Art höchstens einmal."""

    loan = models.ForeignKey(
        Loan, on_delete=models.PROTECT, related_name="events", verbose_name="Ausleihe"
    )
    kind = models.CharField("Vorgang", max_length=20, choices=LoanEventKind.choices)
    at = models.DateTimeField("Zeitpunkt", default=timezone.now)
    actor = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="+",
        null=True,
        blank=True,
        verbose_name="angemeldete Person",
    )
    actor_name = models.CharField(
        "Person laut Altsystem",
        max_length=200,
        blank=True,
        help_text="Nur bei Übernahme aus dem Altsystem, das ausgebende und rücknehmende Person "
        "als Text speichert.",
    )
    quick_check = models.BooleanField("Kurzkontrolle", default=False)
    gal_provided = models.BooleanField("GAL bereitgestellt", default=False)
    self_issued = models.BooleanField("Selbstausleihe", default=False)
    comment = models.TextField("Kommentar", blank=True)
    signature = models.ForeignKey(
        Signature,
        on_delete=models.PROTECT,
        related_name="events",
        null=True,
        blank=True,
        verbose_name="Unterschrift",
    )

    class Meta:
        verbose_name = "Vorgang an einer Ausleihe"
        verbose_name_plural = "Vorgänge an Ausleihen"
        ordering = ("at", "id")
        constraints = (
            models.CheckConstraint(
                condition=Q(kind__in=LoanEventKind.values), name="lending_event_kind_valid"
            ),
            models.UniqueConstraint(fields=["loan", "kind"], name="lending_event_once_per_kind"),
        )

    def __str__(self) -> str:
        return f"{self.get_kind_display()}: {self.loan}"
