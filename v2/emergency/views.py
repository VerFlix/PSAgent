"""Notbetrieb (Parität 22): Notfallmappe erzeugen, herunterladen und offline bereithalten.

Die Mappe nennt Entleiher (nur mit Namen) und ist darum erst ab Verleiher erreichbar. Öffentlich
ist nur das Skript des Service Workers; es enthält keine Daten.
"""

from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.http import FileResponse, HttpRequest, HttpResponse, HttpResponseNotModified
from django.http.response import HttpResponseBase
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from accounts.models import User
from accounts.rights import Right, public, requires
from emergency import binder as service
from emergency.models import Binder

SERVICE_WORKER = Path(settings.BASE_DIR) / "static" / "js" / "notfallmappe-sw.js"


def _etag(binder: Binder) -> str:
    return f'"{binder.sha256}"'


def _download(binder: Binder, *, attachment: bool = True) -> FileResponse:
    stamp = timezone.localtime(binder.created_at)
    response = FileResponse(
        service.path_of(binder).open("rb"),
        as_attachment=attachment,
        filename=f"notfallmappe-{stamp:%Y-%m-%d-%H%M}.pdf",
        content_type="application/pdf",
    )
    response["Cache-Control"] = "no-store"
    response["ETag"] = _etag(binder)
    # Für die Anzeige „Offline gespeichert: …“ auf dem Gerät.
    response["X-Notfallmappe-Stand"] = f"{stamp:%d.%m.%Y %H:%M}"
    return response


@requires(Right.LEND)
@require_GET
def index(request: HttpRequest) -> HttpResponse:
    latest = service.latest_binder()
    context = {
        "latest": latest,
        "ranges": service.ranges(latest) if latest else [],
        "sheets": service.SHEETS_PER_BINDER,
        "keep": service.KEEP_FILES,
    }
    return render(request, "emergency/index.html", context)


@requires(Right.LEND)
@require_POST
def create(request: HttpRequest) -> FileResponse:
    """Erzeugt eine neue Mappe mit neuen Formularnummern und lädt sie herunter."""
    assert isinstance(request.user, User)  # noqa: S101 – von requires() geprüft
    return _download(service.create_binder(actor=request.user))


@requires(Right.LEND)
@require_GET
def latest(request: HttpRequest) -> HttpResponseBase:
    """Die zuletzt gespeicherte Mappe zum Ansehen; dieselbe Adresse liefert offline die Kopie."""
    binder = service.latest_binder()
    if binder is None:
        messages.error(request, "Noch keine Notfallmappe erzeugt.")
        return redirect("emergency:index")
    if request.headers.get("If-None-Match") == _etag(binder):
        response = HttpResponseNotModified()
        response["ETag"] = _etag(binder)
        return response
    return _download(binder, attachment=False)


@public
@require_GET
def service_worker(request: HttpRequest) -> HttpResponse:
    """Skript des Service Workers; unter /notbetrieb/, damit es die Mappe abfangen darf."""
    response = HttpResponse(
        SERVICE_WORKER.read_bytes(), content_type="application/javascript; charset=utf-8"
    )
    response["Cache-Control"] = "no-cache"
    return response
