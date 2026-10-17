"""Notbetrieb (Parität 22): Notfallmappe erzeugen, herunterladen und offline bereithalten.

Die Mappe nennt Entleiher (nur mit Namen) und ist darum erst ab Verleiher erreichbar. Öffentlich
ist nur das Skript des Service Workers; es enthält keine Daten.
"""

from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.http import FileResponse, HttpRequest, HttpResponse, HttpResponseNotModified
from django.http.response import HttpResponseBase
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from accounts.models import User
from accounts.rights import Right, has_right, public, requires
from emergency import backfill
from emergency import binder as service
from emergency.forms import CaptureForm, LoanFormForm, LockRowFormSet, NoteForm, ResolveForm
from emergency.models import Binder, Clarification, LoanForm, PaperForm

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
        "may_clarify": has_right(request.user, Right.CLARIFY),
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


# --- Nacherfassung ---


def _actor(request: HttpRequest) -> User:
    assert isinstance(request.user, User)  # noqa: S101 – von requires() geprüft
    return request.user


@requires(Right.BACKFILL_LOANS)
def backfill_locks(request: HttpRequest) -> HttpResponse:
    """Formular „Sperrung“ mit Scan nacherfassen."""
    form = CaptureForm(request.POST or None, request.FILES or None)
    rows = LockRowFormSet(request.POST or None, prefix="rows")
    if request.method == "POST" and form.is_valid() and rows.is_valid():
        filled = [row.cleaned_data for row in rows if row.cleaned_data]
        scans = [
            (upload.read(), upload.name or "Datei") for upload in request.FILES.getlist("scan")
        ]
        try:
            paper = backfill.capture_locks(
                form.cleaned_data["number"], scans, filled, actor=_actor(request)
            )
        except backfill.BackfillError as error:
            messages.error(request, str(error))
        else:
            messages.success(request, f"Formular {paper.sheet.label} nacherfasst.")
            return redirect("emergency:backfill_locks")
    elif request.method == "POST":
        messages.error(request, "Bitte die markierten Felder prüfen.")
    return render(request, "emergency/backfill_locks.html", {"form": form, "rows": rows})


@requires(Right.BACKFILL_LOANS)
def backfill_loans(request: HttpRequest) -> HttpResponse:
    """Formular „Ausgabe und Rückgabe“ mit Scan nacherfassen."""
    form = CaptureForm(request.POST or None, request.FILES or None)
    fields = LoanFormForm(request.POST or None)
    if request.method == "POST" and form.is_valid() and fields.is_valid():
        scans = [
            (upload.read(), upload.name or "Datei") for upload in request.FILES.getlist("scan")
        ]
        try:
            paper = backfill.capture_loan_form(
                form.cleaned_data["number"], scans, fields.backfill_data(), actor=_actor(request)
            )
        except backfill.BackfillError as error:
            messages.error(request, str(error))
        else:
            loan_form = paper.loan_form
            if not hasattr(loan_form, "result"):
                messages.warning(
                    request,
                    f"Formular {paper.sheet.label} erfasst; wegen einer Doppelbuchung wartet die "
                    "Ausgabe auf die Klärungsliste.",
                )
            else:
                messages.success(request, f"Formular {paper.sheet.label} nacherfasst.")
            return redirect("emergency:backfill_loans")
    elif request.method == "POST":
        messages.error(request, "Bitte die markierten Felder prüfen.")
    return render(request, "emergency/backfill_loans.html", {"form": form, "fields": fields})


@requires(Right.CLARIFY)
@require_POST
def apply_loan_form(request: HttpRequest, pk: int) -> HttpResponse:
    """Doppelbuchung klären: Reservierung stornieren und Ausgabe vom Papier übernehmen."""
    loan_form = get_object_or_404(LoanForm, pk=pk)
    form = NoteForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Bitte eine Begründung angeben.")
        return redirect("emergency:clarifications")
    try:
        backfill.apply_loan_form(loan_form, note=form.cleaned_data["note"], actor=_actor(request))
    except backfill.BackfillError as error:
        messages.error(request, str(error))
    else:
        messages.success(request, "Reservierung storniert und Ausgabe eingetragen.")
    return redirect("emergency:clarifications")


@requires(Right.CLARIFY)
def clarifications(request: HttpRequest) -> HttpResponse:
    """Klärungsliste für PSA-Prüfer oder Admin."""
    if request.method == "POST":
        form = ResolveForm(request.POST)
        if form.is_valid():
            try:
                backfill.resolve(
                    form.cleaned_data["key"], note=form.cleaned_data["note"], actor=_actor(request)
                )
            except backfill.BackfillError as error:
                messages.error(request, str(error))
            else:
                messages.success(request, "Fall geklärt.")
                return redirect("emergency:clarifications")
        else:
            messages.error(request, "Bitte eine Begründung angeben.")
    context = {
        "cases": backfill.open_cases(),
        "resolved": Clarification.objects.select_related("resolved_by")[:20],
        "forms": PaperForm.objects.select_related("sheet", "recorded_by")[:50],
    }
    return render(request, "emergency/clarifications.html", context)


@requires(Right.CLARIFY)
@require_GET
def scan(request: HttpRequest, pk: int) -> FileResponse:
    """Scan eines nacherfassten Formulars, im Browser angezeigt."""
    paper = get_object_or_404(PaperForm.objects.select_related("sheet"), pk=pk)
    response = FileResponse(
        backfill.scan_path(paper).open("rb"),
        content_type="application/pdf",
        as_attachment=False,
        filename=f"formular-{paper.sheet.label}.pdf",
    )
    # Der Scan läuft ohne Skripte und Formulare, auch wenn eine Datei welche enthielte.
    response["Content-Security-Policy"] = "sandbox; default-src 'none'; object-src 'self'"
    response["X-Content-Type-Options"] = "nosniff"
    return response
