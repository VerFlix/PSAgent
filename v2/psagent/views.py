"""Projektweite Views, die zu keiner Fach-App gehören."""

from django.db import DatabaseError, connection
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET

from accounts.rights import Right, public, requires


@requires(Right.ACCOUNT)
@require_GET
def home(request: HttpRequest) -> HttpResponse:
    """Startseite nach der Anmeldung. Die Fachbereiche kommen mit den Paritäts-Issues dazu."""
    return render(request, "home.html")


@public
@require_GET
def healthz(request: HttpRequest) -> JsonResponse:
    """Lebenszeichen für das Monitoring: App läuft und Datenbank antwortet.

    Gibt bewusst keine Details zu Version oder Fehlerursache preis.
    """
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except DatabaseError:
        return JsonResponse({"status": "error", "database": "unreachable"}, status=503)
    return JsonResponse({"status": "ok", "database": "ok"})
