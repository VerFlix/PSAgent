"""Formulare des Datenschutzes."""

from django import forms


class HoldForm(forms.Form):
    reason = forms.CharField(
        label="Grund",
        max_length=1000,
        help_text="Konkreter Schadensfall oder Rechtsstreit, z. B. „Unfall am 12.03., Anfrage "
        "der Versicherung“.",
    )
