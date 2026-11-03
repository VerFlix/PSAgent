"""Formulare der Theke (#127)."""

from django import forms

from counter.damage import REASONS
from lending.forms import PersonForm as PersonForm  # Person anlegen wie unter „Verleih“ (#125)
from privacy.notice import NO_NAMES


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


class DamageForm(forms.Form):
    """Grund zum Antippen und Kommentar; die Pflichten prüft `damage.lock_comment`."""

    reason = forms.ChoiceField(
        label="Grund",
        choices=[(reason, reason) for reason in REASONS],
        widget=forms.RadioSelect,
        required=False,
    )
    comment = forms.CharField(
        label="Was ist beschädigt?",
        max_length=1000,
        required=False,
        widget=forms.Textarea(attrs={"rows": 2}),
        help_text=NO_NAMES,
    )
