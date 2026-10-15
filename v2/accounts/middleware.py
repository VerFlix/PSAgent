"""Middleware: erkennt Vereinsgeräte und hält dort die Sitzung kurz."""

from collections.abc import Callable

from django.conf import settings
from django.http import HttpRequest, HttpResponse

from accounts.devices import identify


class ClubDeviceMiddleware:
    """Setzt `request.club_device`; dort läuft die Sitzung nach kurzer Inaktivität ab."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        device = identify(request)
        request.club_device = device  # type: ignore[attr-defined]
        if device is not None and request.user.is_authenticated:
            # Jede Anfrage verlängert um die kurze Frist; ohne Anfrage endet die Sitzung.
            request.session.set_expiry(settings.PSAGENT_CLUB_DEVICE_IDLE_SECONDS)
        return self.get_response(request)
