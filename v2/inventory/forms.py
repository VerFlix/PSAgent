"""Formulare der Stammdaten: Produkt und System anlegen, GAL, Sperren (Parität 1, 2)."""

from typing import TYPE_CHECKING, Any, ClassVar

from django import forms

from inventory.models import Component, normalize_ei
from inventory.services import COMPONENT_FIELDS, PPE_COMPONENT_FIELDS

GAL_MAX_BYTES = 20 * 1024 * 1024

# Die Typparameter kennen nur die Stubs; zur Laufzeit sind die Klassen nicht generisch.
if TYPE_CHECKING:
    _ModelForm = forms.ModelForm[Component]
    _FormSet = forms.BaseFormSet["ComponentForm"]
else:
    _ModelForm = forms.ModelForm
    _FormSet = forms.BaseFormSet


def ei_taken(ei: str, *, exclude: int | None = None) -> bool:
    """EI schon vergeben? Ohne Groß-/Kleinschreibung und Leerzeichen (wie der Datenbank-Index).

    `exclude` ist das Bauteil, das gerade bearbeitet wird; seine eigene EI zählt nicht.
    """
    others = Component.objects.filter(ei_key=normalize_ei(ei))
    if exclude is not None:
        others = others.exclude(pk=exclude)
    return others.exists()


class ComponentForm(_ModelForm):
    """Die 9 Felder eines Bauteils; die EI ist Pflicht und eindeutig."""

    class Meta:
        model = Component
        fields = COMPONENT_FIELDS
        widgets: ClassVar[dict[str, forms.Widget]] = {
            "purchase_date": forms.DateInput(attrs={"type": "date"}),
            "first_use_date": forms.DateInput(attrs={"type": "date"}),
        }

    def clean_ei(self) -> str:
        ei: str = self.cleaned_data["ei"].strip()
        if not normalize_ei(ei):
            raise forms.ValidationError("Bitte eine EI angeben.")
        if ei_taken(ei, exclude=self.instance.pk):
            raise forms.ValidationError(f"Die EI „{ei}“ ist schon vergeben.")
        return ei

    def values(self) -> dict[str, Any]:
        return {name: self.cleaned_data[name] for name in COMPONENT_FIELDS}


class BaseComponentFormSet(_FormSet):
    """Bauteile eines Systems: leere Zeilen zählen nicht, mindestens eines, EIs verschieden."""

    def clean(self) -> None:
        if any(self.errors):
            return
        filled = [form for form in self.forms if form.has_changed()]
        if not filled:
            raise forms.ValidationError("Ein System braucht mindestens ein Bauteil.")
        keys = [normalize_ei(form.cleaned_data["ei"]) for form in filled]
        if len(set(keys)) != len(keys):
            raise forms.ValidationError("Zwei Bauteile haben dieselbe EI.")


# Drei leere Zeilen wie im Altsystem; weitere holt htmx (`system_component_row`).
ComponentFormSet = forms.formset_factory(
    ComponentForm, formset=BaseComponentFormSet, extra=3, max_num=30
)


# Bauteile eines bestehenden Teils bearbeiten (Parität 3); Bauteile kommen nicht hinzu.
ComponentEditFormSet = forms.modelformset_factory(
    Component, form=ComponentForm, fields=COMPONENT_FIELDS, extra=0, edit_only=True
)


def lock_ppe_fields(formset: Any) -> None:
    """Ohne Sachkunde: EI, Herstellungsjahr und Datum 1. Einsatz nur anzeigen."""
    for form in formset:
        for name in PPE_COMPONENT_FIELDS:
            form.fields[name].disabled = True
            form.fields[name].help_text = "Nur mit gültiger Sachkunde änderbar."


class GalForm(forms.Form):
    """GAL als PDF oder Link; die Datei wird nur einmal gespeichert (Parität 1, 4)."""

    gal_file = forms.FileField(
        label="GAL als PDF", required=False, help_text="Wird nur einmal gespeichert."
    )
    gal_url = forms.URLField(label="oder GAL als Link", required=False, assume_scheme="https")
    gal_title = forms.CharField(label="Titel der GAL", max_length=200, required=False)

    def clean_gal_file(self) -> Any:
        upload = self.cleaned_data.get("gal_file")
        if upload is not None and upload.size > GAL_MAX_BYTES:
            raise forms.ValidationError("Die GAL-Datei ist größer als 20 MB.")
        return upload

    def clean(self) -> dict[str, Any]:
        data = super().clean() or {}
        if data.get("gal_file") and data.get("gal_url"):
            raise forms.ValidationError("Bitte entweder eine Datei oder einen Link angeben.")
        return data


class GalRequiredForm(GalForm):
    """Beim Zuweisen an mehrere Teile ist eine GAL Pflicht."""

    def clean(self) -> dict[str, Any]:
        data = super().clean()
        if not data.get("gal_file") and not data.get("gal_url") and not self.errors:
            raise forms.ValidationError("Bitte eine GAL-Datei oder einen Link angeben.")
        return data


class PartOptionsForm(GalForm):
    """Was für Produkt und System beim Anlegen gleich ist: GAL und „keine PSA“."""

    no_ppe = forms.BooleanField(
        label="keine PSA",
        required=False,
        help_text="Nur mit gültiger Sachkunde. Das Teil ist dann ohne PSA-Prüfung verleihbar.",
    )


class ProductForm(forms.Form):
    name = forms.CharField(
        label="Bezeichnung des Teils",
        max_length=200,
        required=False,
        help_text="Leer: Produktbezeichnung und Produktname.",
    )


class SystemForm(forms.Form):
    name = forms.CharField(label="Name des Systems", max_length=200)


class PartEditForm(forms.Form):
    """Bezeichnung und „keine PSA“ eines bestehenden Teils (Parität 3)."""

    name = forms.CharField(label="Bezeichnung des Teils", max_length=200)
    no_ppe = forms.BooleanField(
        label="keine PSA", required=False, help_text="Nur mit gültiger Sachkunde änderbar."
    )


class CommentForm(forms.Form):
    comment = forms.CharField(label="Kommentar", max_length=1000)
