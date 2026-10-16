"""Notbetrieb (Parität 22): Notfallmappe erzeugen und herunterladen.

Die Mappe nennt Entleiher (nur mit Namen) und ist darum erst ab Verleiher erreichbar.
"""

from django.contrib import messages
from django.http import FileResponse, HttpRequest, HttpResponse
from django.http.response import HttpResponseBase
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from accounts.models import User
from accounts.rights import Right, requires
from emergency import binder as service
from emergency.models import Binder


def _download(binder: Binder) -> FileResponse:
    stamp = timezone.localtime(binder.created_at)
    response = FileResponse(
        service.path_of(binder).open("rb"),
        as_attachment=True,
        filename=f"notfallmappe-{stamp:%Y-%m-%d-%H%M}.pdf",
        content_type="application/pdf",
    )
    response["Cache-Control"] = "no-store"
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
    """Die zuletzt gespeicherte Mappe (auch für die Offline-Kopie auf Vereinsgeräten)."""
    binder = service.latest_binder()
    if binder is None:
        messages.error(request, "Noch keine Notfallmappe erzeugt.")
        return redirect("emergency:index")
    return _download(binder)
