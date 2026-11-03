"""Formulare der Theke (#127)."""

from django import forms

from lending.forms import PersonForm as PersonForm  # Person anlegen wie unter „Verleih“ (#125)


class ContactForm(forms.Form):
    """Neue Kontaktdaten einer bekannten Person; leere Felder bleiben unverändert (Parität 16)."""

    name = forms.CharField(label="Name", max_length=200, required=False)
    address = forms.CharField(
        label="Adresse", required=False, widget=forms.Textarea(attrs={"rows": 2})
    )
    phone = forms.CharField(label="Telefon", max_length=50, required=False)
    email = forms.EmailField(label="E-Mail", required=False)

    def given(self) -> dict[str, str]:
        return {key: str(value).strip() for key, value in self.cleaned_data.items() if value}
