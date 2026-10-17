"""Formulare des Verleihs: Zeitraum, Entleiher, Ausgabe (Parität 12–16)."""

import json
from datetime import date, timedelta
from typing import Any

from django import forms

from lending import services
from privacy.notice import NO_NAMES


class PeriodForm(forms.Form):
    """Zeitraum und Filter nach Bezeichnung (Parität 12: Datumsauswahl statt Texteingabe)."""

    start = forms.DateField(label="von", widget=forms.DateInput(attrs={"type": "date"}))
    end = forms.DateField(
        label="bis einschließlich", widget=forms.DateInput(attrs={"type": "date"})
    )
    q = forms.CharField(label="Filter (Bezeichnung, EI, Seriennummer)", required=False)

    @classmethod
    def initial_for_today(cls) -> dict[str, date]:
        today = services.today()
        return {"start": today, "end": today + timedelta(days=1)}

    def clean(self) -> dict[str, Any]:
        data = super().clean() or {}
        start, end = data.get("start"), data.get("end")
        if start and end:
            if end < start:
                raise forms.ValidationError("„bis“ liegt vor „von“.")
            if start < services.today():
                raise forms.ValidationError("Der Zeitraum beginnt in der Vergangenheit.")
        return data


class BorrowerForm(forms.Form):
    """Bekannter Entleiher (aus der Suche) oder neuer; Kontaktdaten eingeben oder bestätigen.

    Bei bekannten Entleihern ersetzen ausgefüllte Felder die gespeicherten Werte, leere lassen
    sie unverändert. Verleiher sehen die gespeicherten Werte nicht (Rechtetabelle).
    """

    borrower = forms.IntegerField(required=False, widget=forms.HiddenInput)
    name = forms.CharField(label="Name", max_length=200, required=False)
    address = forms.CharField(
        label="Adresse", required=False, widget=forms.Textarea(attrs={"rows": 2})
    )
    email = forms.EmailField(label="E-Mail", required=False)
    phone = forms.CharField(label="Telefon", max_length=50, required=False)
    contact_confirmed = forms.BooleanField(
        label="Kontaktdaten unverändert (nachgefragt)", required=False
    )

    def contact_fields(self) -> dict[str, str]:
        return {key: self.cleaned_data[key] for key in services.CONTACT_FIELDS}

    def has_new_values(self) -> bool:
        return any(value.strip() for value in self.contact_fields().values())

    def clean(self) -> dict[str, Any]:
        data = super().clean() or {}
        if not data.get("borrower") and not (data.get("name") or "").strip():
            raise forms.ValidationError("Bitte einen Entleiher wählen oder einen Namen eingeben.")
        return data


RECEIPT_LABEL = "Quittung per E-Mail an den Entleiher"


class CheckoutForm(forms.Form):
    """Pflichtangaben der Ausgabe wie im Altsystem (Parität 14)."""

    quick_check = forms.BooleanField(label="Kurzkontrolle durchgeführt", required=False)
    gal_provided = forms.BooleanField(label="GAL bereitgestellt", required=False)
    signer_name = forms.CharField(label="Name der unterschreibenden Person", max_length=200)
    signature = forms.CharField(widget=forms.HiddenInput, required=False)
    receipt = forms.BooleanField(label=RECEIPT_LABEL, required=False)

    def clean_signature(self) -> Any:
        raw = self.cleaned_data.get("signature") or ""
        try:
            strokes = json.loads(raw) if raw else []
        except ValueError as error:
            raise forms.ValidationError("Die Unterschrift ist unlesbar.") from error
        if not strokes:
            raise forms.ValidationError("Bitte im Feld unterschreiben.")
        return strokes


class ReturnForm(forms.Form):
    """Rückgabe wie im Altsystem: Kommentar, Kurzprüfung, Unterschrift, optional sperren (17)."""

    comment = forms.CharField(
        label="Kommentar",
        required=False,
        widget=forms.Textarea(attrs={"rows": 2}),
        help_text=NO_NAMES,
    )
    quick_check = forms.BooleanField(label="Kurzprüfung durchgeführt", required=False)
    signer_name = forms.CharField(label="Name der unterschreibenden Person", max_length=200)
    signature = forms.CharField(widget=forms.HiddenInput, required=False)
    receipt = forms.BooleanField(label=RECEIPT_LABEL, required=False)
    lock_reason = forms.CharField(
        label="Grund der Sperre (für markierte Teile)",
        max_length=1000,
        required=False,
        help_text=NO_NAMES,
    )

    clean_signature = CheckoutForm.clean_signature

    def clean_quick_check(self) -> bool:
        if not self.cleaned_data.get("quick_check"):
            raise forms.ValidationError("Bitte die Kurzprüfung bestätigen.")
        return True


class CancelForm(forms.Form):
    comment = forms.CharField(label="Grund", max_length=1000, required=False)
