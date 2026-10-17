"""Projektweite Views, die zu keiner Fach-App gehören."""

import logging

from django.db import DatabaseError, connection
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.utils.cache import add_never_cache_headers
from django.views.decorators.http import require_GET, require_http_methods

from accounts.rights import Right, has_right, public, requires
from psagent import backup


@requires(Right.ACCOUNT)
@require_GET
def home(request: HttpRequest) -> HttpResponse:
    """Startseite nach der Anmeldung. Die Fachbereiche kommen mit den Paritäts-Issues dazu."""
    return render(
        request,
        "home.html",
        {
            "may_manage_users": has_right(request.user, Right.MANAGE_USERS),
            "may_lend": has_right(request.user, Right.LEND),
            "may_hold": has_right(request.user, Right.ERASURE_HOLD),
        },
    )


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


audit = logging.getLogger("psagent.audit")


@requires(Right.MANAGE_USERS)
@require_http_methods(["GET", "POST"])
def backup_view(request: HttpRequest) -> HttpResponse:
    """Parität 11: vollständige Sicherung als ZIP; nur Admin, jeder Abruf wird protokolliert."""
    if request.method == "GET":
        return render(request, "backup.html")
    content = backup.build()
    audit.warning("Vollständige Sicherung heruntergeladen von %s", request.user.get_username())
    stamp = timezone.localtime()
    response = HttpResponse(content, content_type="application/zip")
    response["Content-Disposition"] = (
        f'attachment; filename="psagent-sicherung-{stamp:%Y-%m-%d-%H%M}.zip"'
    )
    add_never_cache_headers(response)
    return response
