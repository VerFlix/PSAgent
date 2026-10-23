"""Stammdaten im Browser: Liste, Detail, anlegen, bearbeiten, GAL, Sperren (Parität 1–4, 21).

Rechte (Rechtetabelle): Liste und Detail sieht jede angemeldete Rolle (keine Personendaten),
anlegen ab Trainer/Materialwart, sperren ab Verleiher, entsperren und „keine PSA“ nur mit
gültiger Sachkunde. Die Regeln selbst prüfen die Dienste in `inventory.services`.
"""

import io
from typing import Any

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.db.models import Prefetch, QuerySet
from django.http import FileResponse, HttpRequest, HttpResponse
from django.http.response import HttpResponseBase
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.text import slugify
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from accounts.models import User
from accounts.rights import Right, has_right, requires
from inspections.services import next_inspection_due
from inventory import documents, export, labels
from inventory.forms import (
    CommentForm,
    ComponentEditFormSet,
    ComponentForm,
    ComponentFormSet,
    GalForm,
    GalRequiredForm,
    PartEditForm,
    PartOptionsForm,
    ProductForm,
    SystemForm,
    lock_ppe_fields,
)
from inventory.models import (
    Component,
    Document,
    EventKind,
    Part,
    PartEvent,
    PartStatus,
    normalize_ei,
)
from inventory.services import (
    InventoryError,
    attach_document,
    create_product,
    create_system,
    link_document,
    record_event,
    rename_part,
    search_parts,
    store_document_file,
    update_component,
)
from inventory.timeline import actor_name, timeline

COMPONENTS = "bauteile"


def _actor(request: HttpRequest) -> User:
    assert isinstance(request.user, User)  # noqa: S101 – von requires() geprüft
    return request.user


def _parts() -> QuerySet[Part]:
    return Part.objects.prefetch_related(
        Prefetch("components", queryset=Component.objects.order_by("position")), "events"
    ).order_by("name", "id")


@requires(Right.VIEW_AVAILABILITY)
@require_GET
def part_list(request: HttpRequest) -> HttpResponse:
    query = request.GET.get("q", "").strip()
    wanted = request.GET.get("status", "")
    rows = []
    for part in search_parts(_parts(), query):
        status = part.status()
        if (wanted and status != wanted) or (not wanted and status == PartStatus.RETIRED):
            continue
        rows.append({"part": part, "status": status, "next_due": next_inspection_due(part)})
    context = {
        "rows": rows,
        "query": query,
        "wanted": wanted,
        "statuses": PartStatus.choices,
        "may_create": has_right(request.user, Right.MANAGE_PARTS),
    }
    return render(request, "inventory/part_list.html", context)


@requires(Right.VIEW_AVAILABILITY)
@require_GET
def part_archive(request: HttpRequest) -> HttpResponse:
    """Parität 10: ausgesonderte Teile, neueste zuerst, mit Datum und Grund.

    Wer ausgesondert hat, sehen Rollen ab Verleiher (Felix, 22.10.2026). Historie und
    Haftungsausschluss stehen auf der Teileseite.
    """
    query = request.GET.get("q", "").strip()
    with_names = has_right(request.user, Right.LEND)
    events = (
        PartEvent.objects.filter(kind=EventKind.RETIRED, part__in=search_parts(_parts(), query))
        .select_related("part", "actor")
        .prefetch_related(
            Prefetch("part__components", queryset=Component.objects.order_by("position"))
        )
        .order_by("-at", "-id")
    )
    rows = [
        {
            "part": event.part,
            "at": event.at,
            "reason": event.comment,
            "actor": actor_name(event.actor, "Altsystem") if with_names else "",
        }
        for event in events
    ]
    return render(
        request,
        "inventory/part_archive.html",
        {"rows": rows, "query": query, "with_names": with_names},
    )


@requires(Right.VIEW_AVAILABILITY)
@require_GET
def part_detail(request: HttpRequest, pk: int) -> HttpResponse:
    part = get_object_or_404(_parts(), pk=pk)
    status = part.status()
    user = request.user
    context: dict[str, Any] = {
        "part": part,
        "status": status,
        "next_due": next_inspection_due(part),
        "no_ppe": part.has_no_ppe(),
        "timeline": timeline(part, with_names=has_right(user, Right.LEND)),
        "documents": part.documents.order_by("title"),
        "may_lock": status not in (PartStatus.LOCKED, PartStatus.RETIRED)
        and has_right(user, Right.LOCK),
        "may_unlock": status == PartStatus.LOCKED and has_right(user, Right.INSPECT),
        "may_inspect": status != PartStatus.RETIRED and has_right(user, Right.INSPECT),
        "may_edit": status != PartStatus.RETIRED and has_right(user, Right.MANAGE_PARTS),
        "may_usage_log": has_right(user, Right.MANAGE_PARTS),
        "may_ppe_documents": has_right(user, Right.PPE_DOCUMENTS),
        "may_lend": status != PartStatus.RETIRED and has_right(user, Right.LEND),
        "comment_form": CommentForm(),
    }
    return render(request, "inventory/part_detail.html", context)


# --- Anlegen ---


def _store_gal(form: GalForm, default_title: str, actor: User) -> Document | None:
    """Speichert die GAL aus dem Formular (Datei nur einmal) oder legt den Link an."""
    data = form.cleaned_data
    title = data["gal_title"].strip() or default_title
    upload = data.get("gal_file")
    if upload is not None:
        return store_document_file(upload.read(), upload.name, title=title, actor=actor)
    if data.get("gal_url"):
        return link_document(data["gal_url"], title=title, actor=actor)
    return None


def _finish(part: Part, options: PartOptionsForm, actor: User) -> None:
    """GAL anhängen und ggf. „keine PSA“ setzen; Fehler brechen die ganze Anlage ab."""
    document = _store_gal(options, f"GAL {part.name}", actor)
    if document is not None:
        part.documents.add(document)
    if options.cleaned_data["no_ppe"]:
        record_event(part, EventKind.NO_PPE_SET, actor=actor)


def _create(request: HttpRequest, options: PartOptionsForm, build: Any) -> Part | None:
    """Legt das Teil in einer Transaktion an; bei Fehlern landet die Meldung im Formular."""
    actor = _actor(request)
    if options.cleaned_data["no_ppe"] and not has_right(actor, Right.PPE_FIELDS):
        options.add_error("no_ppe", "„keine PSA“ setzen geht nur mit gültiger Sachkunde.")
        return None
    try:
        with transaction.atomic():
            part: Part = build(actor)
            _finish(part, options, actor)
    except (InventoryError, PermissionDenied) as error:
        options.add_error(None, str(error))
    except IntegrityError:
        # Gleichzeitig vergeben: der Datenbank-Index ist die letzte Sicherung.
        options.add_error(None, "Eine EI ist inzwischen vergeben. Bitte prüfen.")
    else:
        return part
    return None


@requires(Right.MANAGE_PARTS)
@require_http_methods(["GET", "POST"])
def product_create(request: HttpRequest) -> HttpResponse:
    """Parität 1: Produkt mit 9 Feldern, GAL und „keine PSA“; EI Pflicht und eindeutig."""
    form = ProductForm(request.POST or None)
    component = ComponentForm(request.POST or None, prefix="bauteil")
    options = PartOptionsForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid() and component.is_valid() and options.is_valid():
        part = _create(
            request,
            options,
            lambda actor: create_product(
                component.values(), name=form.cleaned_data["name"].strip() or None, actor=actor
            ),
        )
        if part is not None:
            messages.success(request, f"{part} angelegt; verleihbar nach der ersten PSA-Prüfung.")
            return redirect("inventory:part_detail", pk=part.pk)
    context = {"form": form, "component": component, "options": options}
    return render(request, "inventory/product_form.html", context)


@requires(Right.MANAGE_PARTS)
@require_http_methods(["GET", "POST"])
def system_create(request: HttpRequest) -> HttpResponse:
    """Parität 2: System mit beliebig vielen Bauteilen (Altsystem: fest 3)."""
    form = SystemForm(request.POST or None)
    components = ComponentFormSet(request.POST or None, prefix=COMPONENTS)
    options = PartOptionsForm(request.POST or None, request.FILES or None)
    if (
        request.method == "POST"
        and form.is_valid()
        and components.is_valid()
        and options.is_valid()
    ):
        part = _create(
            request,
            options,
            lambda actor: create_system(
                form.cleaned_data["name"].strip(),
                [row.values() for row in components.forms if row.has_changed()],
                actor=actor,
            ),
        )
        if part is not None:
            messages.success(request, f"{part} angelegt; verleihbar nach der ersten PSA-Prüfung.")
            return redirect("inventory:part_detail", pk=part.pk)
    context = {"form": form, "components": components, "options": options}
    return render(request, "inventory/system_form.html", context)


@requires(Right.MANAGE_PARTS)
@require_GET
def system_component_row(request: HttpRequest) -> HttpResponse:
    """htmx: eine weitere leere Bauteilzeile; der Zähler des Formsets steigt mit (out of band)."""
    try:
        index = int(request.GET.get(f"{COMPONENTS}-TOTAL_FORMS", ""))
    except ValueError:
        index = ComponentFormSet.min_num + ComponentFormSet.extra
    index = max(0, min(index, ComponentFormSet.max_num - 1))
    row = ComponentForm(prefix=f"{COMPONENTS}-{index}")
    context = {"row": row, "number": index + 1, "total": index + 1, "prefix": COMPONENTS}
    return render(request, "inventory/_component_row.html", context)


# --- Sperren und Entsperren ---


def _event(request: HttpRequest, pk: int, kind: EventKind, done: str) -> HttpResponse:
    part = get_object_or_404(Part, pk=pk)
    form = CommentForm(request.POST)
    if not form.is_valid():
        messages.error(request, f"„{kind.label}“ braucht einen Kommentar.")
        return redirect("inventory:part_detail", pk=part.pk)
    try:
        record_event(part, kind, actor=_actor(request), comment=form.cleaned_data["comment"])
    except InventoryError as error:
        messages.error(request, str(error))
    else:
        messages.success(request, f"{part} {done}.")
    return redirect("inventory:part_detail", pk=part.pk)


@requires(Right.LOCK)
@require_POST
def part_lock(request: HttpRequest, pk: int) -> HttpResponse:
    """Sperren: jede Rolle ab Verleiher, jederzeit, mit Pflichtkommentar."""
    return _event(request, pk, EventKind.LOCKED, "gesperrt")


@requires(Right.INSPECT)
@require_POST
def part_unlock(request: HttpRequest, pk: int) -> HttpResponse:
    """Entsperren: nur mit gültiger Sachkunde, mit Pflichtkommentar."""
    return _event(request, pk, EventKind.UNLOCKED, "entsperrt")


# --- Bearbeiten (Parität 3) ---


def _edit_forms(request: HttpRequest, part: Part) -> tuple[PartEditForm, Any, bool]:
    may_ppe = has_right(request.user, Right.PPE_FIELDS)
    data = request.POST or None
    initial = {"name": part.name, "no_ppe": part.has_no_ppe()}
    form = PartEditForm(data, initial=initial)
    if not may_ppe:
        form.fields["no_ppe"].disabled = True
    components = ComponentEditFormSet(
        data, prefix=COMPONENTS, queryset=part.components.order_by("position")
    )
    if not may_ppe:
        lock_ppe_fields(components)
    return form, components, may_ppe


def _shown(value: Any) -> str:
    if value in (None, ""):
        return "–"
    if value is True:
        return "ja"
    if value is False:
        return "nein"
    if hasattr(value, "strftime"):
        return str(value.strftime("%d.%m.%Y"))
    return str(value)


def _changes(form: PartEditForm, components: Any) -> list[dict[str, str]]:
    """Was sich ändern würde, für die Bestätigung: Bereich, Feld, alt, neu."""
    rows = []
    for name in form.changed_data:
        rows.append(
            {
                "where": "Teil",
                "field": str(form.fields[name].label),
                "old": _shown(form.initial.get(name)),
                "new": _shown(form.cleaned_data[name]),
            }
        )
    for row in components:
        for name in row.changed_data:
            rows.append(
                {
                    "where": f"Bauteil {row.initial.get('ei') or row.instance.pk}",
                    "field": str(row.fields[name].label),
                    "old": _shown(row.initial.get(name)),
                    "new": _shown(row.cleaned_data[name]),
                }
            )
    return rows


def _apply(part: Part, form: PartEditForm, components: Any, actor: User) -> None:
    """Speichert alle Änderungen in einer Transaktion; die Dienste prüfen die Rechte."""
    with transaction.atomic():
        if "name" in form.changed_data:
            rename_part(part, form.cleaned_data["name"], actor=actor)
        for row in components:
            if row.changed_data:
                original = Component.objects.get(pk=row.instance.pk, part=part)
                changes = {name: row.cleaned_data[name] for name in row.changed_data}
                update_component(original, actor=actor, **changes)
        if "no_ppe" in form.changed_data:
            kind = EventKind.NO_PPE_SET if form.cleaned_data["no_ppe"] else EventKind.NO_PPE_CLEARED
            record_event(part, kind, actor=actor)


@requires(Right.MANAGE_PARTS)
@require_http_methods(["GET", "POST"])
def part_edit(request: HttpRequest, pk: int) -> HttpResponse:
    """Parität 3: bearbeiten mit Bestätigung; PSA-relevante Felder nur mit Sachkunde.

    „Nächste Prüfung“ ist hier nicht änderbar; sie setzt nur eine Prüfung.
    """
    part = get_object_or_404(Part, pk=pk)
    if part.is_retired():
        messages.error(request, f"{part} ist ausgesondert; Änderungen sind nicht möglich.")
        return redirect("inventory:part_detail", pk=part.pk)
    form, components, may_ppe = _edit_forms(request, part)
    context: dict[str, Any] = {
        "part": part,
        "form": form,
        "components": components,
        "may_ppe": may_ppe,
    }
    if request.method == "POST" and form.is_valid() and components.is_valid():
        changes = _changes(form, components)
        if not changes:
            messages.info(request, "Keine Änderungen.")
            return redirect("inventory:part_detail", pk=part.pk)
        if "back" in request.POST:
            return render(request, "inventory/part_edit.html", context)
        if "confirm" not in request.POST:
            context["changes"] = changes
            return render(request, "inventory/part_edit_confirm.html", context)
        try:
            _apply(part, form, components, _actor(request))
        except (InventoryError, PermissionDenied) as error:
            messages.error(request, str(error))
        except IntegrityError:
            messages.error(request, "Eine EI ist inzwischen vergeben. Bitte prüfen.")
        else:
            messages.success(request, f"Änderungen an {part} gespeichert.")
            return redirect("inventory:part_detail", pk=part.pk)
    return render(request, "inventory/part_edit.html", context)


# --- GAL (Parität 4, 21) ---


@requires(Right.MANAGE_PARTS)
@require_http_methods(["GET", "POST"])
def gal_assign(request: HttpRequest) -> HttpResponse:
    """Parität 4: eine GAL an mehrere markierte Teile; die Datei wird nur einmal gespeichert."""
    ids = [int(value) for value in request.GET.getlist("teil") if value.isdigit()]
    ids += [int(value) for value in request.POST.getlist("teil") if value.isdigit()]
    parts = list(_parts().filter(pk__in=ids))
    if not parts:
        messages.error(request, "Bitte in der Liste mindestens ein Teil markieren.")
        return redirect("inventory:part_list")
    form = GalRequiredForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        actor = _actor(request)
        try:
            with transaction.atomic():
                document = _store_gal(form, "GAL", actor)
                assert document is not None  # noqa: S101 – GalRequiredForm verlangt eine GAL
                added = attach_document(parts, document, actor=actor)
        except InventoryError as error:
            form.add_error(None, str(error))
        else:
            messages.success(request, f"GAL „{document.title}“ an {added} Teil(e) angehängt.")
            return redirect("inventory:part_list")
    return render(request, "inventory/gal_assign.html", {"form": form, "parts": parts})


@requires(Right.VIEW_AVAILABILITY)
@require_GET
def document_open(request: HttpRequest, pk: int) -> HttpResponseBase:
    """Parität 21: GAL direkt im Browser, auf jedem Gerät; Links führen zum Hersteller."""
    document = get_object_or_404(Document, pk=pk)
    if document.url:
        return redirect(document.url)
    response = FileResponse(
        document.file.open("rb"),
        content_type="application/pdf",
        as_attachment=False,
        filename=f"{slugify(document.title) or 'gal'}.pdf",
    )
    # Das PDF läuft ohne Skripte und Formulare, auch wenn eine Datei welche enthielte.
    response["Content-Security-Policy"] = "sandbox; default-src 'none'; object-src 'self'"
    response["X-Content-Type-Options"] = "nosniff"
    return response


# --- Export (Parität 11) ---


@requires(Right.VIEW_AVAILABILITY)
@require_GET
def part_export(request: HttpRequest) -> HttpResponse:
    """Parität 11: alle oder markierte Teile als CSV oder PDF, ohne Personendaten."""
    kind = request.GET.get("format", "csv")
    if kind not in ("csv", "pdf"):
        kind = "csv"
    ids = [int(value) for value in request.GET.getlist("teil") if value.isdigit()]
    marked = bool(ids)
    with_retired = marked or request.GET.get("ausgesondert") == "1"
    parts = export.parts_for_export(ids if marked else None, with_retired=with_retired)
    data = export.rows(parts)
    stand = timezone.localdate()
    if kind == "pdf":
        title = "Teileliste (markierte Teile)" if marked else "Teileliste"
        content = export.to_pdf(data, title=title, stand=f"{stand:%d.%m.%Y}")
        content_type = "application/pdf"
    else:
        content = export.to_csv(data)
        content_type = "text/csv; charset=utf-8"
    response = HttpResponse(content, content_type=content_type)
    response["Content-Disposition"] = f'attachment; filename="teileliste-{stand:%Y-%m-%d}.{kind}"'
    return response


# --- PDF-Dokumente je Teil (Parität 5) ---


def _document(request: HttpRequest, pk: int, kind: documents.DocumentKind) -> FileResponse:
    part = get_object_or_404(Part, pk=pk)
    assert isinstance(request.user, User)  # noqa: S101 – von requires() geprüft
    content = documents.render(kind, part, user=request.user)
    return FileResponse(
        io.BytesIO(content),
        as_attachment=True,
        filename=documents.filename(kind, part),
        content_type="application/pdf",
    )


@requires(Right.MANAGE_PARTS)
@require_GET
def part_usage_log(request: HttpRequest, pk: int) -> FileResponse:
    """Leere Einsatzdokumentation mit dem Verein als Besitzer."""
    return _document(request, pk, documents.DocumentKind.USAGE_LOG)


@requires(Right.PPE_DOCUMENTS)
@require_GET
def part_data_sheet(request: HttpRequest, pk: int) -> FileResponse:
    """PSA-Produktdatenblatt mit Stempel der erzeugenden Person (nur mit Sachkunde)."""
    return _document(request, pk, documents.DocumentKind.DATA_SHEET)


@requires(Right.PPE_DOCUMENTS)
@require_GET
def part_disclaimer(request: HttpRequest, pk: int) -> FileResponse:
    """Haftungsausschluss mit Name und Stempel der erzeugenden Person (nur mit Sachkunde)."""
    return _document(request, pk, documents.DocumentKind.DISCLAIMER)


# --- QR-Etiketten (#14) ---


@requires(Right.MANAGE_PARTS)
@require_GET
def part_labels(request: HttpRequest) -> HttpResponse:
    """Etiketten für markierte Teile oder für alle (ohne ausgesonderte) als PDF."""
    ids = [int(value) for value in request.GET.getlist("teil") if value.isdigit()]
    parts = _parts()
    if ids:
        parts = parts.filter(pk__in=ids)
    raw = request.GET.get("start", "1")
    start = int(raw) if raw.isdigit() else 1
    content = labels.render(labels.entries(parts), start=start)
    response = HttpResponse(content, content_type="application/pdf")
    stamp = timezone.localdate()
    response["Content-Disposition"] = f'attachment; filename="qr-etiketten-{stamp:%Y-%m-%d}.pdf"'
    return response


@requires(Right.VIEW_AVAILABILITY)
@require_GET
def scan_target(request: HttpRequest, ei: str) -> HttpResponse:
    """Ziel des QR-Codes: zur Teileseite; unbekannte und ausgesonderte EI werden abgewiesen."""
    component = Component.objects.select_related("part").filter(ei_key=normalize_ei(ei)).first()
    if component is None:
        return render(
            request, "inventory/scan_unknown.html", {"ei": ei, "retired": False}, status=404
        )
    if component.part.is_retired():
        return render(
            request,
            "inventory/scan_unknown.html",
            {"ei": ei, "retired": True, "part": component.part},
            status=410,
        )
    return redirect("inventory:part_detail", component.part.pk)
