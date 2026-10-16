"""Formulare der Nacherfassung (Parität 22)."""

from typing import Any

from django import forms

from emergency import backfill

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


class ResolveForm(forms.Form):
    key = forms.CharField(max_length=100)
    note = forms.CharField(label="Klärung", max_length=1000)
