"""Stammdaten im Browser: Teileliste, Detail, Produkt und System anlegen, Sperren (Parität 1, 2).

Rechte (Rechtetabelle): Liste und Detail sieht jede angemeldete Rolle (keine Personendaten),
anlegen ab Trainer/Materialwart, sperren ab Verleiher, entsperren und „keine PSA“ nur mit
gültiger Sachkunde. Die Regeln selbst prüfen die Dienste in `inventory.services`.
"""

from typing import Any

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.db.models import Prefetch, Q, QuerySet
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from accounts.models import User
from accounts.rights import Right, has_right, requires
from inspections.services import next_inspection_due
from inventory.forms import (
    CommentForm,
    ComponentForm,
    ComponentFormSet,
    PartOptionsForm,
    ProductForm,
    SystemForm,
)
from inventory.models import Component, EventKind, Part, PartStatus, normalize_ei
from inventory.services import (
    InventoryError,
    create_product,
    create_system,
    link_document,
    record_event,
    store_document_file,
)

COMPONENTS = "bauteile"


def _actor(request: HttpRequest) -> User:
    assert isinstance(request.user, User)  # noqa: S101 – von requires() geprüft
    return request.user


def _parts() -> QuerySet[Part]:
    return Part.objects.prefetch_related(
        Prefetch("components", queryset=Component.objects.order_by("position")), "events"
    ).order_by("name", "id")


def _search(parts: QuerySet[Part], query: str) -> QuerySet[Part]:
    """Suche über Bezeichnung, EI (ohne Leerzeichen/Großschreibung), Hersteller, Seriennummer."""
    if not query:
        return parts
    match = (
        Q(name__icontains=query)
        | Q(components__designation__icontains=query)
        | Q(components__model_name__icontains=query)
        | Q(components__manufacturer__icontains=query)
        | Q(components__serial_number__icontains=query)
    )
    key = normalize_ei(query)
    if key:
        match |= Q(components__ei_key__contains=key)
    return parts.filter(match).distinct()


@requires(Right.VIEW_AVAILABILITY)
@require_GET
def part_list(request: HttpRequest) -> HttpResponse:
    query = request.GET.get("q", "").strip()
    wanted = request.GET.get("status", "")
    rows = []
    for part in _search(_parts(), query):
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
def part_detail(request: HttpRequest, pk: int) -> HttpResponse:
    part = get_object_or_404(_parts(), pk=pk)
    status = part.status()
    user = request.user
    context: dict[str, Any] = {
        "part": part,
        "status": status,
        "next_due": next_inspection_due(part),
        "no_ppe": part.has_no_ppe(),
        "events": part.events.select_related("actor").order_by("-at", "-id"),
        "documents": part.documents.order_by("title"),
        "may_lock": status not in (PartStatus.LOCKED, PartStatus.RETIRED)
        and has_right(user, Right.LOCK),
        "may_unlock": status == PartStatus.LOCKED and has_right(user, Right.INSPECT),
        "comment_form": CommentForm(),
    }
    return render(request, "inventory/part_detail.html", context)


# --- Anlegen ---


def _finish(part: Part, options: PartOptionsForm, actor: User) -> None:
    """GAL anhängen und ggf. „keine PSA“ setzen; Fehler brechen die ganze Anlage ab."""
    data = options.cleaned_data
    title = data["gal_title"].strip() or f"GAL {part.name}"
    upload = data.get("gal_file")
    if upload is not None:
        part.documents.add(
            store_document_file(upload.read(), upload.name, title=title, actor=actor)
        )
    elif data.get("gal_url"):
        part.documents.add(link_document(data["gal_url"], title=title, actor=actor))
    if data["no_ppe"]:
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
