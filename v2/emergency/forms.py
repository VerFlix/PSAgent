"""Formulare der Nacherfassung (Parität 22)."""

from typing import Any

from django import forms

from accounts.models import Role, User
from emergency import backfill
from inspections.models import Outcome
from privacy.notice import NO_NAMES

DATE = forms.DateInput(attrs={"type": "date"})


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    """Mehrere Dateien unter einem Namen (Django-Dokumentation, „Uploading multiple files“)."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("widget", MultipleFileInput())
        super().__init__(*args, **kwargs)

    def clean(self, data: Any, initial: Any = None) -> Any:
        single = super().clean
        if isinstance(data, list | tuple):
            return [single(item, initial) for item in data]
        return [single(data, initial)] if data else []


class CaptureForm(forms.Form):
    number = forms.CharField(label="Formularnummer", max_length=20)
    scan = MultipleFileField(label="Scan", required=False)


class LockRowForm(forms.Form):
    ei = forms.CharField(label="Teil (EI)", max_length=100)
    reason = forms.CharField(label="Grund", max_length=1000)
    locked_on = forms.DateField(label="Datum", widget=DATE)
    locked_by_name = forms.CharField(label="Name sperrende Person", max_length=200)


LockRowFormSet = forms.formset_factory(
    LockRowForm, extra=3, max_num=backfill.MAX_LOCK_ROWS, absolute_max=backfill.MAX_LOCK_ROWS
)


class ExpertChoiceField(forms.ModelChoiceField):  # type: ignore[type-arg]  # zur Laufzeit nicht generisch
    """Auswahl eines PSA-Prüfers, angezeigt mit vollem Namen."""

    def label_from_instance(self, obj: Any) -> str:
        return str(obj.get_full_name() or obj.get_username())


class InspectionHeaderForm(forms.Form):
    """Kopf des Formulars „Prüfprotokoll“: Datum und PSA-Prüfer laut Papier (#80)."""

    performed_on = forms.DateField(label="Datum der Prüfung", widget=DATE)
    inspector = ExpertChoiceField(
        label="PSA-Prüfer laut Formular", queryset=User.objects.none(), empty_label=None
    )

    def __init__(self, *args: Any, actor: User, may_choose: bool, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        field = self.fields["inspector"]
        assert isinstance(field, ExpertChoiceField)  # noqa: S101 – oben so angelegt
        if may_choose:
            experts = User.objects.filter(
                is_active=True, role__in=[Role.ADMIN, Role.PPE_EXPERT]
            ).order_by("last_name", "first_name")
            field.queryset = experts
        else:
            field.queryset = User.objects.filter(pk=actor.pk)
        field.initial = actor.pk


class InspectionRowForm(forms.Form):
    ei = forms.CharField(label="Teil (EI)", max_length=100)
    # Leere Vorauswahl, damit unberührte Zeilen leer bleiben und übersprungen werden.
    outcome = forms.ChoiceField(label="Ergebnis", choices=[("", "–"), *Outcome.choices])
    comment = forms.CharField(label="Kommentar", max_length=1000, required=False)
    next_due = forms.DateField(label="nächste Prüfung", required=False, widget=DATE)


InspectionRowFormSet = forms.formset_factory(
    InspectionRowForm,
    extra=backfill.MAX_INSPECTION_ROWS,
    max_num=backfill.MAX_INSPECTION_ROWS,
    absolute_max=backfill.MAX_INSPECTION_ROWS,
)


class ResolveForm(forms.Form):
    key = forms.CharField(max_length=100)
    note = forms.CharField(label="Klärung", max_length=1000)


class LoanFormForm(forms.Form):
    """Formular „Ausgabe und Rückgabe“: Felder wie auf dem Papier."""

    eis = forms.CharField(
        label="Teile (EI), je Zeile eine",
        widget=forms.Textarea(attrs={"rows": 4}),
        max_length=2000,
    )
    quick_check = forms.BooleanField(label="Kurzkontrolle angekreuzt", required=False)
    gal_provided = forms.BooleanField(label="GAL bereitgestellt angekreuzt", required=False)
    issued_on = forms.DateField(label="Datum Ausgabe", required=False, widget=DATE)
    planned_return = forms.DateField(label="Rückgabe geplant am", required=False, widget=DATE)
    borrower_name = forms.CharField(label="Entleiher: Name", required=False, max_length=200)
    address = forms.CharField(
        label="Entleiher: Adresse",
        required=False,
        max_length=500,
        widget=forms.Textarea(attrs={"rows": 2}),
    )
    phone = forms.CharField(label="Entleiher: Telefon", required=False, max_length=50)
    email = forms.EmailField(label="Entleiher: E-Mail", required=False)
    issued_by_name = forms.CharField(label="Name ausgebende Person", required=False, max_length=200)
    returned_on = forms.DateField(label="Datum Rückgabe", required=False, widget=DATE)
    return_quick_check = forms.BooleanField(label="Kurzkontrolle bei Rückgabe", required=False)
    return_comment = forms.CharField(
        label="Rückgabe-Kommentar",
        required=False,
        max_length=2000,
        widget=forms.Textarea(attrs={"rows": 2}),
        help_text=NO_NAMES,
    )
    lock_reason = forms.CharField(
        label="Sperrung nötig: Grund (leer, wenn nicht angekreuzt)", required=False, max_length=1000
    )
    received_by_name = forms.CharField(
        label="Name rücknehmende Person", required=False, max_length=200
    )

    def backfill_data(self) -> dict[str, Any]:
        data = dict(self.cleaned_data)
        data["eis"] = [line.strip() for line in data["eis"].splitlines() if line.strip()]
        return data


class NoteForm(forms.Form):
    note = forms.CharField(label="Begründung", max_length=1000)
