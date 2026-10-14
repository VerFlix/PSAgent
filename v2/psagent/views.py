"""Projektweite Views, die zu keiner Fach-App gehören."""

from django.db import DatabaseError, connection
from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_GET


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
