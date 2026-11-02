"""Anmeldung, Abmeldung, Konto mit Passkeys und Vereinsgeräte (#11).

Rechte im Server (`accounts.rights`): Jede Ansicht trägt `requires(...)` oder `public`.
Öffentlich sind nur Anmeldung, Abmeldung und der Einrichtungslink; Vereinsgeräte verwaltet nur
ein Admin. Über den Einrichtungslink richten Admin und PSA-Sachkundige einen Passkey ein, die
übrigen Rollen wahlweise einen Passkey oder ein eigenes Passwort (#67).
"""

import json
from typing import Any

from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth.forms import SetPasswordForm
from django.contrib.auth.signals import user_login_failed
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods, require_POST

from accounts import confirm, counter, services, webauthn
from accounts.cards import counter_card
from accounts.confirm import requires_yubikey
from accounts.devices import register_device, revoke_device
from accounts.enrollment import find_token, mark_used
from accounts.models import ClubDevice, Passkey, User
from accounts.rights import Right, has_right, may_use, public, requires

# Backend, unter dem die Sitzung läuft (auch nach Passkey-Anmeldung).
SESSION_BACKEND = "django.contrib.auth.backends.ModelBackend"
WRONG_CREDENTIALS = "Benutzername oder Passwort ist falsch."
COUNTER_ONLY = (
    "An der Theke melden sich nur Verleiher und Trainer/Materialwart an; Admin und "
    "PSA-Sachkundige nutzen dort ein Verleiher-Konto."
)


class LoginForm(forms.Form):
    username = forms.CharField(label="Benutzername", max_length=150)
    password = forms.CharField(label="Passwort", widget=forms.PasswordInput)


class DeviceForm(forms.Form):
    name = forms.CharField(label="Bezeichnung des Geräts", max_length=100)
    theke = forms.BooleanField(
        label="Theke (QR-Code und PIN, höchstens Verleiher-Rechte)", required=False
    )


class CounterLoginForm(forms.Form):
    code = forms.CharField(label="Persönlicher QR-Code", max_length=200)
    pin = forms.CharField(label="PIN", max_length=20)


class CounterPinForm(forms.Form):
    pin = forms.CharField(label="Neue PIN (6 Ziffern)", max_length=6, min_length=6)
    repeat = forms.CharField(label="PIN wiederholen", max_length=6, min_length=6)


class StampForm(forms.Form):
    # Die genaue Grenze prüft `services.set_stamp`; hier nur gegen übergroße Anfragen.
    stamp_text = forms.CharField(
        label="Briefkopf/Stempel", required=False, max_length=2000, widget=forms.Textarea
    )


def save_stamp(request: HttpRequest, account: User) -> None:
    """Speichert den Stempel aus dem Formular und meldet Erfolg oder Fehler."""
    form = StampForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Der Briefkopf/Stempel ist zu lang.")
        return
    assert isinstance(request.user, User)  # noqa: S101 – von requires() geprüft
    try:
        services.set_stamp(account, form.cleaned_data["stamp_text"], actor=request.user)
    except services.AccountError as error:
        messages.error(request, str(error))
    else:
        messages.success(request, "Briefkopf/Stempel gespeichert.")


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


@public
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
        elif _refused_at_counter(request, user):
            form.add_error(None, COUNTER_ONLY)
        else:
            login(request, user, backend=SESSION_BACKEND)
            return redirect(_next_url(request))
    context = {
        "form": form,
        "next": request.GET.get("next", ""),
        "counter_form": CounterLoginForm() if _at_counter(request) else None,
    }
    return render(request, "accounts/login.html", context)


def _at_counter(request: HttpRequest) -> bool:
    device = getattr(request, "club_device", None)
    return device is not None and device.counter


def _refused_at_counter(request: HttpRequest, user: User) -> bool:
    """Theke (#126): nur Verleiher und Trainer/Materialwart melden sich dort an."""
    return _at_counter(request) and not may_use(user, Right.COUNTER_LOGIN)


@public
@require_http_methods(["GET", "POST"])
def counter_login(request: HttpRequest) -> HttpResponse:
    """Anmeldung an der Theke mit persönlichem QR-Code und PIN (#126)."""
    form = CounterLoginForm(request.POST or None)
    error = ""
    if request.method == "POST" and form.is_valid():
        try:
            user = counter.authenticate(
                getattr(request, "club_device", None),
                form.cleaned_data["code"],
                form.cleaned_data["pin"],
            )
        except counter.CounterError as problem:
            error = str(problem)
        else:
            login(request, user, backend=SESSION_BACKEND)
            request.session[counter.PIN_SESSION] = True
            return redirect(_next_url(request))
    context = {"counter_form": form, "counter_error": error, "at_counter": _at_counter(request)}
    return render(request, "accounts/counter_login.html", context)


@public
@require_POST
def passkey_login_options(request: HttpRequest) -> HttpResponse:
    return HttpResponse(
        webauthn.authentication_options(request.session), content_type="application/json"
    )


@public
@require_POST
def passkey_login(request: HttpRequest) -> JsonResponse:
    data = _json_body(request)
    try:
        user = webauthn.authenticate(data.get("credential", {}), request.session)
    except webauthn.PasskeyError as error:
        return _error(str(error))
    if _refused_at_counter(request, user):
        return _error(COUNTER_ONLY)
    login(request, user, backend=SESSION_BACKEND)
    return JsonResponse({"redirect": _next_url(request)})


@public
@require_POST
def logout_view(request: HttpRequest) -> HttpResponse:
    logout(request)
    return redirect(settings.LOGOUT_REDIRECT_URL)


# --- Konto und Passkeys ---


@requires(Right.ACCOUNT)
@require_http_methods(["GET"])
def account(request: HttpRequest) -> HttpResponse:
    user = _current_user(request)
    context: dict[str, Any] = {
        "passkeys": user.passkeys.filter(revoked_at__isnull=True),
        "has_yubikey": webauthn.yubikeys(user).exists(),
        "device_form": DeviceForm(),
    }
    if has_right(user, Right.MANAGE_USERS):
        context["devices"] = ClubDevice.objects.filter(revoked_at__isnull=True).order_by("name")
    if has_right(user, Right.COUNTER_CREDENTIALS):
        credential = getattr(user, "counter_credential", None)
        context["counter"] = {
            "form": CounterPinForm(),
            "has_pin": bool(credential and credential.pin_hash),
            "locked": bool(credential and credential.locked_at),
            "has_qr": bool(credential and credential.qr_hash),
            "in_pin_session": bool(request.session.get(counter.PIN_SESSION)),
        }
    return render(request, "accounts/account.html", context)


def _not_in_pin_session(request: HttpRequest) -> None:
    """PIN und QR-Code nur nach Anmeldung mit Passwort oder Passkey, nie per PIN (#126)."""
    if request.session.get(counter.PIN_SESSION):
        raise PermissionDenied(
            "PIN und QR-Code richtest du nach der Anmeldung mit Passwort oder Passkey ein, "
            "nicht in einer Sitzung per PIN."
        )


@requires(Right.COUNTER_CREDENTIALS)
@require_POST
def counter_pin(request: HttpRequest) -> HttpResponse:
    """Eigene PIN für die Theke setzen; hebt eine Sperre auf."""
    _not_in_pin_session(request)
    form = CounterPinForm(request.POST)
    if not form.is_valid() or form.cleaned_data["pin"] != form.cleaned_data["repeat"]:
        messages.error(request, "Bitte zweimal dieselbe PIN mit 6 Ziffern eingeben.")
        return redirect("accounts:account")
    try:
        counter.set_pin(
            _current_user(request), form.cleaned_data["pin"], actor=_current_user(request)
        )
    except counter.CounterError as error:
        messages.error(request, str(error))
    else:
        messages.success(request, "PIN für die Theke gesetzt.")
    return redirect("accounts:account")


@requires(Right.COUNTER_CREDENTIALS)
@require_POST
def counter_qr(request: HttpRequest) -> HttpResponse:
    """Neuer persönlicher QR-Code als Karte (PDF); der alte gilt danach nicht mehr."""
    _not_in_pin_session(request)
    user = _current_user(request)
    code = counter.issue_qr(user, actor=user)
    response = HttpResponse(counter_card(user, code), content_type="application/pdf")
    response["Content-Disposition"] = 'attachment; filename="Theke-Karte.pdf"'
    response["Cache-Control"] = "no-store"
    return response


@requires(Right.ACCOUNT)
@require_POST
def stamp(request: HttpRequest) -> HttpResponse:
    """Eigenen Briefkopf/Stempel ändern; immer das angemeldete Konto."""
    save_stamp(request, _current_user(request))
    return redirect("accounts:account")


@requires(Right.ACCOUNT)
@require_POST
def passkey_register_options(request: HttpRequest) -> HttpResponse:
    user = _current_user(request)
    return HttpResponse(
        webauthn.registration_options(user, request.session), content_type="application/json"
    )


@requires(Right.ACCOUNT)
@require_POST
@confirm.when_yubikey_present
def passkey_register(request: HttpRequest) -> JsonResponse:
    """Weiterer Passkey. Hat das Konto einen freigegebenen YubiKey, nur mit dessen Bestätigung.

    Sonst könnte jemand, der eine fremde Sitzung übernimmt, seinen eigenen YubiKey eintragen und
    damit Änderungen in der Nutzerverwaltung bestätigen (#95).
    """
    user = _current_user(request)
    data = _json_body(request)
    approved = webauthn.yubikeys(user).exists()
    try:
        if approved:
            webauthn.confirm(user, data.get(confirm.FIELD) or {}, request.session, request.path)
    except webauthn.PasskeyError:
        return _error("Einen weiteren Passkey bestätigen Sie mit Ihrem YubiKey.", status=403)
    try:
        webauthn.register(
            user,
            data.get("credential", {}),
            str(data.get("name", "")),
            request.session,
            approved=approved,
        )
    except webauthn.PasskeyError as error:
        return _error(str(error))
    messages.success(request, "Passkey eingerichtet.")
    return JsonResponse({"redirect": reverse("accounts:account")})


@requires(Right.ACCOUNT)
@require_POST
def passkey_revoke(request: HttpRequest, pk: int) -> HttpResponse:
    passkey = get_object_or_404(Passkey, pk=pk, user=request.user, revoked_at__isnull=True)
    try:
        webauthn.revoke(passkey)
    except webauthn.PasskeyError as error:
        messages.error(request, str(error))
    else:
        # Andere Sitzungen (etwa mit einem verlorenen Schlüssel geöffnet) enden, diese bleibt.
        user = _current_user(request)
        user.end_sessions()
        update_session_auth_hash(request, user)
        messages.success(request, f"Passkey „{passkey.name}“ widerrufen.")
    return redirect("accounts:account")


# --- Einrichtungslink für den ersten Passkey ---


def _enroll_page(
    token: str, account: User, form: SetPasswordForm[User] | None = None
) -> dict[str, Any]:
    """Passkey immer; ein eigenes Passwort nur für Rollen ohne Passkey-Pflicht."""
    if form is None and not account.requires_passkey:
        form = SetPasswordForm(account)
    return {"token": token, "account": account, "password_form": form}


@public
@require_http_methods(["GET"])
def enroll(request: HttpRequest, token: str) -> HttpResponse:
    entry = find_token(token)
    if entry is None:
        return render(request, "accounts/enroll_invalid.html", status=404)
    return render(request, "accounts/enroll.html", _enroll_page(token, entry.user))


@public
@require_POST
def enroll_options(request: HttpRequest, token: str) -> HttpResponse:
    entry = find_token(token)
    if entry is None:
        return _error("Der Einrichtungslink ist ungültig oder abgelaufen.", status=404)
    return HttpResponse(
        webauthn.registration_options(entry.user, request.session),
        content_type="application/json",
    )


@public
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


@public
@require_POST
def enroll_password(request: HttpRequest, token: str) -> HttpResponse:
    """Eigenes Passwort statt Passkey festlegen; nur für Rollen ohne Passkey-Pflicht.

    Wie beim Passkey: Link sperren, Passwort setzen und Link verbrauchen in einer Transaktion.
    """
    with transaction.atomic():
        entry = find_token(token, lock=True)
        if entry is None:
            return render(request, "accounts/enroll_invalid.html", status=404)
        account = entry.user
        if account.requires_passkey:
            raise PermissionDenied("Admin und PSA-Sachkundige melden sich nur mit Passkey an.")
        form = SetPasswordForm(account, request.POST)
        if not form.is_valid():
            context = _enroll_page(token, account, form)
            return render(request, "accounts/enroll.html", context, status=400)
        form.save()
        mark_used(entry)
    login(request, account, backend=SESSION_BACKEND)
    messages.success(request, "Passwort festgelegt. Sie sind angemeldet.")
    return redirect(settings.LOGIN_REDIRECT_URL)


# --- Vereinsgeräte (nur Admin) ---


@requires(Right.MANAGE_USERS)
@requires_yubikey
@require_POST
def club_device_register(request: HttpRequest) -> HttpResponse:
    admin = _current_user(request)
    form = DeviceForm(request.POST)
    response = redirect("accounts:account")
    if not form.is_valid() or not form.cleaned_data["name"].strip():
        messages.error(request, "Bitte eine Bezeichnung für das Gerät angeben.")
        return response
    device = register_device(
        form.cleaned_data["name"],
        actor=admin,
        response=response,
        counter=form.cleaned_data["theke"],
    )
    messages.success(request, f"Dieses Gerät ist jetzt das Vereinsgerät „{device.name}“.")
    return response


@requires(Right.MANAGE_USERS)
@requires_yubikey
@require_POST
def club_device_revoke(request: HttpRequest, pk: int) -> HttpResponse:
    device = get_object_or_404(ClubDevice, pk=pk, revoked_at__isnull=True)
    revoke_device(device)
    messages.success(request, f"Vereinsgerät „{device.name}“ widerrufen.")
    return redirect("accounts:account")
