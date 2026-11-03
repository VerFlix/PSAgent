"""Theke-Einstellungen (#127, Teil 3): Art-Kürzel, Schnellzugriff, nach Größe, Vorauswahl der
Leihdauer. Nur Admin; jede Änderung bestätigt er mit dem YubiKey (Felix, 03.11.2026)."""

from dataclasses import asdict

from django import forms
from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from accounts.confirm import requires_yubikey
from accounts.rights import Right, requires
from counter import config
from counter.models import CounterSetting
from counter.views import _actor


class KindForm(forms.Form):
    code = forms.CharField(label="Kürzel", max_length=3, required=False)
    label = forms.CharField(label="Name", max_length=40, required=False)
    quick = forms.BooleanField(label="Schnellzugriff", required=False)
    sized = forms.BooleanField(label="nach Größe", required=False)


KindFormSet = forms.formset_factory(KindForm, extra=3, can_delete=True, max_num=config.MAX_KINDS)


class DurationForm(forms.Form):
    default_days = forms.TypedChoiceField(
        label="Vorauswahl der Leihdauer", choices=config.DURATIONS, coerce=int
    )


def _entries(formset: forms.BaseFormSet) -> list[config.Kind]:  # type: ignore[type-arg]
    entries = []
    for data in formset.cleaned_data:
        if not data or data.get("DELETE"):
            continue
        if not (data.get("code") or data.get("label")):
            continue
        entries.append(
            config.Kind(
                code=data.get("code", ""),
                label=data.get("label", ""),
                quick=bool(data.get("quick")),
                sized=bool(data.get("sized")),
            )
        )
    return entries


@requires(Right.COUNTER_SETTINGS)
@requires_yubikey
@require_http_methods(["GET", "POST"])
def counter_settings(request: HttpRequest) -> HttpResponse:
    """Art-Kürzel und Vorauswahl der Dauer; nur angehängt, es gilt der jüngste Stand."""
    data = request.POST or None
    # Startwerte nur beim Anzeigen: Ein gebundenes Formset mit Startwerten verwirft sonst Zeilen,
    # die sich gegenüber den Startwerten nicht geändert haben.
    if data is None:
        formset = KindFormSet(initial=[asdict(kind) for kind in config.kinds()], prefix="art")
    else:
        formset = KindFormSet(data, prefix="art")
    duration = DurationForm(data, initial={"default_days": config.default_days()})
    error = ""
    if request.method == "POST" and formset.is_valid() and duration.is_valid():
        try:
            config.configure(
                _entries(formset), duration.cleaned_data["default_days"], actor=_actor(request)
            )
        except config.SettingError as problem:
            error = str(problem)
        else:
            messages.success(request, "Theke-Einstellungen gespeichert.")
            return redirect("counter:settings")
    context = {
        "formset": formset,
        "duration": duration,
        "error": error,
        "current": config.current(),
        "history": CounterSetting.objects.select_related("actor")[:10],
        "max_quick": config.MAX_QUICK,
    }
    return render(request, "counter/settings.html", context, status=400 if error else 200)
