"""Anmeldung, Abmeldung, Konto mit Passkeys und Vereinsgeräte (#11).

Rechte im Server: Alles außer Anmeldung und Einrichtungslink verlangt ein angemeldetes Konto;
Vereinsgeräte verwaltet nur ein Admin. Die vollständige Rechtetabelle folgt mit #12.
"""

import json
from typing import Any

from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.signals import user_login_failed
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods, require_POST

from accounts import webauthn
from accounts.devices import register_device, revoke_device
from accounts.enrollment import find_token, mark_used
from accounts.models import ClubDevice, Passkey, Role, User

# Backend, unter dem die Sitzung läuft (auch nach Passkey-Anmeldung).
SESSION_BACKEND = "django.contrib.auth.backends.ModelBackend"
WRONG_CREDENTIALS = "Benutzername oder Passwort ist falsch."


class LoginForm(forms.Form):
    username = forms.CharField(label="Benutzername", max_length=150)
    password = forms.CharField(label="Passwort", widget=forms.PasswordInput)


class DeviceForm(forms.Form):
    name = forms.CharField(label="Bezeichnung des Geräts", max_length=100)


def _next_url(request: HttpRequest) -> str:
    target = request.POST.get("next") or request.GET.get("next") or ""
    if target and url_has_allowed_host_and_scheme(
        target, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return target
    return reverse(settings.LOGIN_REDIRECT_URL)


def _json_body(request: HttpRequest) -> dict[str, Any]:
    try:
        data = json.loads(request.body or b"{}")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _current_user(request: HttpRequest) -> User:
    user = request.user
    if not isinstance(user, User):
        raise PermissionDenied("Bitte anmelden.")
    return user


def _error(message: str, status: int = 400) -> JsonResponse:
    return JsonResponse({"error": message}, status=status)


# --- Anmeldung ---


@require_http_methods(["GET", "POST"])
def login_view(request: HttpRequest) -> HttpResponse:
    """Anmeldung mit Passwort; die Passkey-Anmeldung läuft über die JSON-Endpunkte unten."""
    form = LoginForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = authenticate(
            request,
            username=form.cleaned_data["username"],
            password=form.cleaned_data["password"],
        )
        if isinstance(user, User) and user.requires_passkey:
            # Wie ein Fehlversuch behandeln: gleiche Meldung, zählt für die Sperre. Sonst
            # verriete die Antwort, dass das Passwort eines Admins richtig ist.
            user_login_failed.send(
                sender=__name__,
                credentials={"username": form.cleaned_data["username"]},
                request=request,
            )
            user = None
        if not isinstance(user, User):
            form.add_error(None, WRONG_CREDENTIALS)
        else:
            login(request, user, backend=SESSION_BACKEND)
            return redirect(_next_url(request))
    return render(
        request, "accounts/login.html", {"form": form, "next": request.GET.get("next", "")}
    )


@require_POST
def passkey_login_options(request: HttpRequest) -> HttpResponse:
    return HttpResponse(
        webauthn.authentication_options(request.session), content_type="application/json"
    )


@require_POST
def passkey_login(request: HttpRequest) -> JsonResponse:
    data = _json_body(request)
    try:
        user = webauthn.authenticate(data.get("credential", {}), request.session)
    except webauthn.PasskeyError as error:
        return _error(str(error))
    login(request, user, backend=SESSION_BACKEND)
    return JsonResponse({"redirect": _next_url(request)})


@require_POST
def logout_view(request: HttpRequest) -> HttpResponse:
    logout(request)
    return redirect(settings.LOGOUT_REDIRECT_URL)


# --- Konto und Passkeys ---


@login_required
@require_http_methods(["GET"])
def account(request: HttpRequest) -> HttpResponse:
    user = _current_user(request)
    context: dict[str, Any] = {
        "passkeys": user.passkeys.filter(revoked_at__isnull=True),
        "device_form": DeviceForm(),
    }
    if user.has_role(Role.ADMIN):
        context["devices"] = ClubDevice.objects.filter(revoked_at__isnull=True).order_by("name")
    return render(request, "accounts/account.html", context)


@login_required
@require_POST
def passkey_register_options(request: HttpRequest) -> HttpResponse:
    user = _current_user(request)
    return HttpResponse(
        webauthn.registration_options(user, request.session), content_type="application/json"
    )


@login_required
@require_POST
def passkey_register(request: HttpRequest) -> JsonResponse:
    user = _current_user(request)
    data = _json_body(request)
    try:
        webauthn.register(
            user, data.get("credential", {}), str(data.get("name", "")), request.session
        )
    except webauthn.PasskeyError as error:
        return _error(str(error))
    messages.success(request, "Passkey eingerichtet.")
    return JsonResponse({"redirect": reverse("accounts:account")})


@login_required
@require_POST
def passkey_revoke(request: HttpRequest, pk: int) -> HttpResponse:
    passkey = get_object_or_404(Passkey, pk=pk, user=request.user, revoked_at__isnull=True)
    try:
        webauthn.revoke(passkey)
    except webauthn.PasskeyError as error:
        messages.error(request, str(error))
    else:
        messages.success(request, f"Passkey „{passkey.name}“ widerrufen.")
    return redirect("accounts:account")


# --- Einrichtungslink für den ersten Passkey ---


@require_http_methods(["GET"])
def enroll(request: HttpRequest, token: str) -> HttpResponse:
    entry = find_token(token)
    if entry is None:
        return render(request, "accounts/enroll_invalid.html", status=404)
    return render(request, "accounts/enroll.html", {"token": token, "account": entry.user})


@require_POST
def enroll_options(request: HttpRequest, token: str) -> HttpResponse:
    entry = find_token(token)
    if entry is None:
        return _error("Der Einrichtungslink ist ungültig oder abgelaufen.", status=404)
    return HttpResponse(
        webauthn.registration_options(entry.user, request.session),
        content_type="application/json",
    )


@require_POST
def enroll_finish(request: HttpRequest, token: str) -> JsonResponse:
    """Richtet den Passkey ein und verbraucht den Link, beides in einer Transaktion.

    Der Link wird zuerst gesperrt; scheitert die Einrichtung, bleibt er unbenutzt. Eine
    gleichzeitige zweite Anfrage findet ihn danach als benutzt.
    """
    data = _json_body(request)
    try:
        with transaction.atomic():
            entry = find_token(token, lock=True)
            if entry is None:
                return _error("Der Einrichtungslink ist ungültig oder abgelaufen.", status=404)
            webauthn.register(
                entry.user, data.get("credential", {}), str(data.get("name", "")), request.session
            )
            mark_used(entry)
    except webauthn.PasskeyError as error:
        return _error(str(error))
    login(request, entry.user, backend=SESSION_BACKEND)
    messages.success(request, "Passkey eingerichtet. Sie sind angemeldet.")
    return JsonResponse({"redirect": reverse("accounts:account")})


# --- Vereinsgeräte (nur Admin) ---


def _require_admin(request: HttpRequest) -> User:
    user = request.user
    if not isinstance(user, User) or not user.has_role(Role.ADMIN):
        raise PermissionDenied("Vereinsgeräte verwaltet nur ein Admin.")
    return user


@login_required
@require_POST
def club_device_register(request: HttpRequest) -> HttpResponse:
    admin = _require_admin(request)
    form = DeviceForm(request.POST)
    response = redirect("accounts:account")
    if not form.is_valid() or not form.cleaned_data["name"].strip():
        messages.error(request, "Bitte eine Bezeichnung für das Gerät angeben.")
        return response
    device = register_device(form.cleaned_data["name"], actor=admin, response=response)
    messages.success(request, f"Dieses Gerät ist jetzt das Vereinsgerät „{device.name}“.")
    return response


@login_required
@require_POST
def club_device_revoke(request: HttpRequest, pk: int) -> HttpResponse:
    _require_admin(request)
    device = get_object_or_404(ClubDevice, pk=pk, revoked_at__isnull=True)
    revoke_device(device)
    messages.success(request, f"Vereinsgerät „{device.name}“ widerrufen.")
    return redirect("accounts:account")
