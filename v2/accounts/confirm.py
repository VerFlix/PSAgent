"""Jede Änderung in der Nutzerverwaltung mit YubiKey bestätigen (#95, Konzept „Schreibschutz“).

Festlegung (Felix, 21.10.2026): Vor jedem Absenden tippt der Admin einen YubiKey an. Es zählt
nur ein Passkey mit geprüfter Herstellerbescheinigung von Yubico (`Passkey.yubikey_attested`);
die Bestätigung gilt einmal und nur für die Adresse, an die das Formular geht. Ohne YubiKey geht
in der Oberfläche nichts; auf dem Server bleiben die Verwaltungsbefehle (`actor=None`).

Im Browser holt `static/js/yubikey.js` die Optionen, fragt den Schlüssel und legt die Antwort
in das Feld `FIELD` des Formulars. Geprüft wird ausschließlich hier im Server.
"""

import json
from collections.abc import Callable
from functools import wraps
from typing import Any

from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.http.response import HttpResponseBase
from django.urls import Resolver404, resolve
from django.views.decorators.http import require_POST

from accounts import webauthn
from accounts.models import User
from accounts.rights import Right, requires

FIELD = "yubikey_assertion"
DENIED = "Diese Änderung braucht eine Bestätigung mit Ihrem YubiKey."


def requires_yubikey[V: Callable[..., HttpResponseBase]](view: V) -> V:
    """Schreibende Anfragen (POST) nur mit frischer YubiKey-Bestätigung für genau diese Adresse."""

    @wraps(view)
    def confirmed(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponseBase:
        if request.method == "POST":
            if not isinstance(request.user, User):
                raise PermissionDenied(DENIED)
            credential = request.POST.get(FIELD, "")
            if not credential:
                raise PermissionDenied(DENIED)
            try:
                webauthn.confirm(request.user, credential, request.session, request.path)
            except webauthn.PasskeyError as error:
                raise PermissionDenied(str(error)) from error
        return view(request, *args, **kwargs)

    confirmed.psagent_yubikey = True  # type: ignore[attr-defined]
    return confirmed  # type: ignore[return-value]


@requires(Right.MANAGE_USERS)
@require_POST
def options(request: HttpRequest) -> HttpResponse:
    """Optionen für eine Bestätigung; nur für Adressen, die eine Bestätigung verlangen."""
    try:
        path = str(json.loads(request.body or b"{}").get("path", ""))
        match = resolve(path)
    except (ValueError, AttributeError, Resolver404):
        return JsonResponse({"error": "Unbekannte Adresse."}, status=400)
    if not getattr(match.func, "psagent_yubikey", False):
        return JsonResponse({"error": "Diese Adresse braucht keine Bestätigung."}, status=400)
    assert isinstance(request.user, User)  # noqa: S101 – von requires() geprüft
    try:
        body = webauthn.confirmation_options(request.user, request.session, path)
    except webauthn.PasskeyError as error:
        return JsonResponse({"error": str(error)}, status=400)
    return HttpResponse(body, content_type="application/json")
