"""Client-Adresse für die Sperre nach Fehlversuchen (django-axes)."""

from django.conf import settings
from django.http import HttpRequest


def client_ip(request: HttpRequest) -> str:
    """Adresse des Clients.

    Hinter einem vertrauenswürdigen Proxy (auf dem Server `tailscale serve`) zählt der letzte
    Eintrag in X-Forwarded-For, den der Proxy selbst setzt. Alles davor kann der Client
    fälschen. Ohne eingetragenen Proxy gilt die Verbindungsadresse.
    """
    remote = str(request.META.get("REMOTE_ADDR", ""))
    if remote in settings.PSAGENT_TRUSTED_PROXIES:
        forwarded = str(request.META.get("HTTP_X_FORWARDED_FOR", ""))
        hops = [hop.strip() for hop in forwarded.split(",") if hop.strip()]
        if hops:
            return hops[-1]
    return remote
