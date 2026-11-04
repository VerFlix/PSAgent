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
  Name und Strichliste der Unterschrift stehen löschbar daneben (`SignatureContent`, #63).
- **Kontaktdaten stehen getrennt** vom Entleiher, damit sie nach der Löschfrist gelöscht werden
  können, ohne die Historie zu zerstören (Konzept, Abschnitte 4 und 7).
"""

import hashlib
import json
import secrets
from datetime import date, datetime, timedelta
from typing import Any

from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import DateRangeField, RangeOperators
from django.db import models, transaction
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
    member_number = models.CharField(
        "Mitgliedsnummer (DAV-Ausweis)",
        max_length=11,
        blank=True,
        help_text="11 Ziffern vom Strichcode des Mitgliedsausweises (#125); nur lokal.",
    )
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
            models.CheckConstraint(
                condition=Q(member_number="") | Q(member_number__regex=r"^[0-9]{11}$"),
                name="lending_contact_member_number_digits",
            ),
            models.UniqueConstraint(
                fields=["member_number"],
                condition=~Q(member_number=""),
                name="lending_contact_member_number_unique",
            ),
        )

    def __str__(self) -> str:
        return self.name

    @property
    def member_number_display(self) -> str:
        """Mitgliedsnummer wie auf dem Ausweis: 123/00/456789 (#125)."""
        number = self.member_number
        return f"{number[:3]}/{number[3:5]}/{number[5:]}" if number else ""

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


def signature_digest(salt: str, signer_name: str, strokes: Any) -> str:
    """SHA-256 über Salt, Name und Strichliste in fester JSON-Form.

    Der zufällige Salt steht nur in der löschbaren Zeile. Ohne ihn lässt sich ein gelöschter
    Name nicht durch Ausprobieren aus der Prüfsumme zurückgewinnen.
    """
    canonical = json.dumps(
        {"salt": salt, "signer_name": signer_name, "strokes": strokes},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


class Signature(AppendOnlyModel):
    """Unterschrift zu einem Vorgang. Wird nur angehängt, enthält aber keine Personendaten.

    Name und Strichliste stehen in `SignatureContent` und werden mit dem Entleiher gelöscht
    (#63). Hier bleibt die Prüfsumme des Inhalts; sie belegt, dass der Vorgang unverändert ist.
    Die Bindung an den Hash des Ausleihdatensatzes folgt mit der Hash-Kette (#15).
    """

    content_sha256 = models.CharField("SHA-256 von Name und Unterschrift", max_length=64)
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
            models.CheckConstraint(
                condition=Q(content_sha256__regex=r"^[0-9a-f]{64}$"),
                name="lending_signature_sha256",
            ),
        )

    def __str__(self) -> str:
        return f"Unterschrift vom {self.created_at:%d.%m.%Y}"

    @classmethod
    def record(
        cls, strokes: Any, signer_name: str, origin: str = SignatureOrigin.APP
    ) -> "Signature":
        """Legt Unterschrift und löschbaren Inhalt gemeinsam an."""
        salt = secrets.token_hex(16)
        with transaction.atomic():
            signature = cls.objects.create(
                content_sha256=signature_digest(salt, signer_name, strokes), origin=origin
            )
            SignatureContent.objects.create(
                signature=signature, salt=salt, signer_name=signer_name, strokes=strokes
            )
        return signature

    @property
    def stored_content(self) -> "SignatureContent | None":
        """Name und Strichliste; `None`, wenn sie nach der Löschfrist gelöscht sind."""
        try:
            return self.content
        except SignatureContent.DoesNotExist:
            return None

    def verify(self) -> bool | None:
        """Passt der Inhalt zur Prüfsumme? `None`, wenn der Inhalt gelöscht ist."""
        content = self.stored_content
        if content is None:
            return None
        return content.digest() == self.content_sha256

    @property
    def is_legacy(self) -> bool:
        return self.origin == SignatureOrigin.LEGACY

    @property
    def is_paper(self) -> bool:
        return self.origin == SignatureOrigin.PAPER


class SignatureContent(models.Model):
    """Name und Strichliste [[[x, y], …], …] einer Unterschrift. Wird mit dem Entleiher gelöscht.

    Nur hier stehen Personendaten der Unterschrift; die Tabelle ist darum nicht „nur anhängen“.
    Eine Änderung fiele auf, weil der Inhalt dann nicht mehr zur Prüfsumme passt.
    """

    signature = models.OneToOneField(
        Signature,
        on_delete=models.CASCADE,
        primary_key=True,
        related_name="content",
        verbose_name="Unterschrift",
    )
    salt = models.CharField("Salt", max_length=32)
    signer_name = models.CharField("Name der unterschreibenden Person", max_length=200)
    strokes = models.JSONField("Striche")

    class Meta:
        verbose_name = "Inhalt einer Unterschrift"
        verbose_name_plural = "Inhalte von Unterschriften"

    def __str__(self) -> str:
        return self.signer_name

    def digest(self) -> str:
        return signature_digest(self.salt, self.signer_name, self.strokes)


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


class Reminder(AppendOnlyModel):
    """Erinnerung bei Überfälligkeit, die per E-Mail rausging (#72). Kein Inhalt, keine Adresse."""

    loan = models.ForeignKey(
        Loan, on_delete=models.PROTECT, related_name="reminders", verbose_name="Ausleihe"
    )
    number = models.PositiveSmallIntegerField("Nummer")
    sent_at = models.DateTimeField("gesendet am", default=timezone.now)
    # Takt nach einer Verlängerung (#129): Die Erinnerungen zählen ab dem neuen Datum neu.
    extension = models.ForeignKey(
        "LoanExtension",
        on_delete=models.PROTECT,
        related_name="+",
        null=True,
        blank=True,
        verbose_name="nach Verlängerung",
    )

    class Meta:
        verbose_name = "Erinnerung"
        verbose_name_plural = "Erinnerungen"
        ordering = ("sent_at", "id")
        constraints = (
            models.UniqueConstraint(
                fields=["loan", "extension", "number"],
                name="lending_reminder_once_per_round",
                nulls_distinct=False,
            ),
            models.CheckConstraint(
                condition=Q(number__gte=1, number__lte=3), name="lending_reminder_number"
            ),
        )

    def __str__(self) -> str:
        return f"{self.number}. Erinnerung zu {self.loan}"


class ReminderSwitch(AppendOnlyModel):
    """Erinnerungen ein- oder ausschalten, für alle (`booking` leer) oder je Vorgang.

    Es gilt der jüngste Eintrag; ohne Eintrag sind Erinnerungen an.
    """

    booking = models.ForeignKey(
        Booking,
        on_delete=models.PROTECT,
        related_name="reminder_switches",
        null=True,
        blank=True,
        verbose_name="Ausleihvorgang",
        help_text="Leer: gilt für alle.",
    )
    enabled = models.BooleanField("Erinnerungen an")
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
        verbose_name = "Schalter für Erinnerungen"
        verbose_name_plural = "Schalter für Erinnerungen"
        ordering = ("-at", "-id")

    def __str__(self) -> str:
        scope = f"Vorgang #{self.booking_id}" if self.booking_id else "alle"
        return f"Erinnerungen {'an' if self.enabled else 'aus'} ({scope})"


class DigestSetting(AppendOnlyModel):
    """Sammelmail über überfällige Ausleihen an Vereinsadressen (#128): Schalter und Empfänger.

    Es gilt der jüngste Eintrag; ohne Eintrag ist die Sammelmail aus. Jede Änderung bestätigt der
    Admin mit dem YubiKey, denn eine neue Adresse leitet Namen von Entleihern weiter.
    """

    enabled = models.BooleanField("Sammelmail an")
    recipients = models.JSONField("Empfänger", default=list, blank=True)
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
        verbose_name = "Einstellung der Sammelmail"
        verbose_name_plural = "Einstellungen der Sammelmail"
        ordering = ("-at", "-id")

    def __str__(self) -> str:
        return f"Sammelmail {'an' if self.enabled else 'aus'} ({len(self.recipients)} Adressen)"


class DigestRun(AppendOnlyModel):
    """Eine gesendete Sammelmail (#128): nur Tag und Anzahlen, kein Inhalt, keine Adresse."""

    day = models.DateField("Stichtag", unique=True)
    bookings = models.PositiveIntegerField("Vorgänge")
    recipients = models.PositiveSmallIntegerField("Empfänger")
    sent_at = models.DateTimeField("gesendet am", default=timezone.now)

    class Meta:
        verbose_name = "Gesendete Sammelmail"
        verbose_name_plural = "Gesendete Sammelmails"
        ordering = ("-day",)

    def __str__(self) -> str:
        return f"Sammelmail {self.day:%d.%m.%Y}: {self.bookings} Vorgänge"


class MemberCardEventKind(models.TextChoices):
    LINKED = "linked", "Mitgliedsausweis verbunden"
    CHANGED = "changed", "Mitgliedsausweis geändert"
    UNLINKED = "unlinked", "Mitgliedsausweis gelöst"


class MemberCardEvent(AppendOnlyModel):
    """Protokoll zum Mitgliedsausweis eines Entleihers (#125): wer, was, wann, ohne die Nummer.

    Die Nummer selbst steht nur in `BorrowerContact` und verschwindet mit der Löschfrist (#63);
    Tabellen, die nur angehängt werden, enthalten keine löschpflichtigen Personendaten.
    """

    borrower = models.ForeignKey(
        Borrower, on_delete=models.PROTECT, related_name="+", verbose_name="Entleiher"
    )
    kind = models.CharField("Vorgang", max_length=10, choices=MemberCardEventKind.choices)
    actor = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="+",
        null=True,
        blank=True,
        verbose_name="durch",
    )
    at = models.DateTimeField("Zeitpunkt", default=timezone.now)

    class Meta:
        verbose_name = "Vorgang zum Mitgliedsausweis"
        verbose_name_plural = "Vorgänge zum Mitgliedsausweis"
        ordering = ("at", "id")
        constraints = (
            models.CheckConstraint(
                condition=Q(kind__in=MemberCardEventKind.values), name="lending_member_card_kind"
            ),
        )

    def __str__(self) -> str:
        return f"{self.get_kind_display()}: Entleiher #{self.borrower_id}"


class ExtensionReason(models.TextChoices):
    PHONE = "phone", "telefonisch"
    IN_PERSON = "in_person", "persönlich"
    EMAIL = "email", "per E-Mail"


class LoanExtension(AppendOnlyModel):
    """Verlängerung einer laufenden Ausleihe (#129). Wird nur angehängt, in der Hash-Kette.

    Der Zeitraum der Ausleihe ändert sich nur über diesen Eintrag (`lending.extensions`); das bei
    der Ausgabe unterschriebene Datum steht im ersten Eintrag als `old_end` bzw. ohne Eintrag in
    der Ausleihe. Keine Unterschrift (Festlegung Felix, 01.11.2026).
    """

    loan = models.ForeignKey(
        Loan, on_delete=models.PROTECT, related_name="extensions", verbose_name="Ausleihe"
    )
    old_end = models.DateField("bisher zurück bis")
    new_end = models.DateField("neu zurück bis")
    reason = models.CharField("Grund", max_length=20, choices=ExtensionReason.choices)
    comment = models.TextField("Kommentar", blank=True)
    actor = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="+",
        null=True,
        blank=True,
        verbose_name="durch",
    )
    at = models.DateTimeField("Zeitpunkt", default=timezone.now)

    class Meta:
        verbose_name = "Verlängerung"
        verbose_name_plural = "Verlängerungen"
        ordering = ("at", "id")
        constraints = (
            models.CheckConstraint(
                condition=Q(new_end__gt=models.F("old_end")), name="lending_extension_later"
            ),
            models.CheckConstraint(
                condition=Q(reason__in=ExtensionReason.values), name="lending_extension_reason"
            ),
        )

    def __str__(self) -> str:
        return f"Verlängerung {self.loan}: {self.old_end} → {self.new_end}"


class ExtensionLimit(AppendOnlyModel):
    """Grenzen für Verlängerungen (#129): Höchstzahl je Ausleihe, Höchstdauer je Verlängerung.

    Es gilt der jüngste Eintrag; ohne Eintrag 1 Verlängerung mit höchstens 14 Tagen. Ändern darf
    nur der Admin, jede Änderung mit dem YubiKey (Festlegung Felix, 01.11.2026).
    """

    max_count = models.PositiveSmallIntegerField("Höchstzahl je Ausleihe")
    max_days = models.PositiveSmallIntegerField("Höchstdauer je Verlängerung (Tage)")
    actor = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="+",
        null=True,
        blank=True,
        verbose_name="durch",
    )
    at = models.DateTimeField("Zeitpunkt", default=timezone.now)

    class Meta:
        verbose_name = "Grenze für Verlängerungen"
        verbose_name_plural = "Grenzen für Verlängerungen"
        ordering = ("-at", "-id")
        constraints = (
            models.CheckConstraint(
                condition=Q(max_count__lte=10, max_days__gte=1, max_days__lte=90),
                name="lending_extension_limit_range",
            ),
        )

    def __str__(self) -> str:
        return f"höchstens {self.max_count}-mal je {self.max_days} Tage"
