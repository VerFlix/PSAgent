"""Middleware: erkennt Vereinsgeräte und hält dort die Sitzung kurz."""

from collections.abc import Callable

from django.conf import settings
from django.contrib.auth import logout
from django.http import HttpRequest, HttpResponse

from accounts.counter import PIN_DEVICE, PIN_SESSION
from accounts.devices import identify
from accounts.models import ClubDevice
from accounts.rights import Right, may_use


def _pin_session_elsewhere(request: HttpRequest, device: ClubDevice | None) -> bool:
    """Eine Sitzung per PIN gilt nur an dem Theke-Gerät, an dem sie begann (#126)."""
    if not request.session.get(PIN_SESSION):
        return False
    return device is None or not device.counter or device.pk != request.session.get(PIN_DEVICE)


class ClubDeviceMiddleware:
    """Setzt `request.club_device`; dort läuft die Sitzung nach kurzer Inaktivität ab."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        device = identify(request)
        request.club_device = device  # type: ignore[attr-defined]
        if request.user.is_authenticated and _pin_session_elsewhere(request, device):
            logout(request)
        if device is not None and request.user.is_authenticated:
            if device.counter and not may_use(request.user, Right.COUNTER_LOGIN):
                # Theke (#126): Admin, PSA-Sachkundige und Viewer melden sich dort nicht an.
                logout(request)
            else:
                # Jede Anfrage verlängert um die kurze Frist; ohne Anfrage endet die Sitzung.
                request.session.set_expiry(settings.PSAGENT_CLUB_DEVICE_IDLE_SECONDS)
                if device.counter:
                    # An der Theke höchstens Verleiher-Rechte, auch per Passwort angemeldet.
                    request.user.counter_cap = True
        return self.get_response(request)
