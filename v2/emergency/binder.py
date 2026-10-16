"""Notfallmappe erzeugen (Parität 22, Konzept Abschnitt 7).

Inhalt:
- alle Teile außer ausgesonderten, je Bauteil eine Zeile mit EI, Bezeichnung, Status,
  nächster Prüfung und GAL-Verweis;
- laufende Ausleihen und Reservierungen der nächsten vier Wochen, Entleiher nur mit Namen
  (die Offline-Kopie liegt auf Vereinsgeräten);
- je Formular eine feste Zahl leerer Blätter mit neuen, fortlaufenden Nummern.

Jede erzeugte Mappe wird mit Prüfsumme gespeichert. Weil sie Namen enthält, bleiben nur die
letzten `KEEP_FILES` Dateien liegen; die Einträge mit den Formularnummern bleiben immer.
"""

import hashlib
import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import typst
from django.conf import settings
from django.db import connection, transaction
from django.db.models import Max, Prefetch
from django.utils import timezone

from accounts.models import User
from accounts.rights import Right, require
from emergency.models import Binder, FormKind, FormSheet
from inspections.services import next_inspection_due
from inventory.export import as_text
from inventory.models import Component, Part, PartStatus
from lending import services as lending
from lending.models import Loan, LoanStatus

TEMPLATE = Path(settings.BASE_DIR) / "templates" / "typst" / "notfallmappe.typ"
SHEETS_PER_BINDER = {FormKind.LOAN: 10, FormKind.LOCK: 5, FormKind.INSPECTION: 5}
LOAN_WINDOW = timedelta(days=28)
KEEP_FILES = 5
# Fester Schlüssel für die Sperre, unter der Formularnummern vergeben werden.
_NUMBER_LOCK = 52_2210


def path_of(binder: Binder) -> Path:
    return Path(settings.MEDIA_ROOT) / "notfallmappe" / f"notfallmappe-{binder.pk}.pdf"


def _parts() -> list[list[str]]:
    parts = Part.objects.prefetch_related(
        Prefetch("components", queryset=Component.objects.order_by("position")),
        "events",
        "documents",
    ).order_by("name", "pk")
    rows = []
    for part in parts:
        status = part.status()
        if status == PartStatus.RETIRED:
            continue
        no_ppe = part.has_no_ppe()
        due = "keine PSA" if no_ppe else as_text(next_inspection_due(part)) or "–"
        gal = "; ".join(
            f"{document.title} ({document.url})" if document.url else document.title
            for document in part.documents.all()
        )
        for component in part.components.all():
            rows.append(
                [
                    component.ei,
                    part.name,
                    component.designation or "–",
                    status.label,
                    due,
                    gal or "keine GAL",
                ]
            )
    return rows


def _loans() -> list[list[str]]:
    horizon = lending.today() + LOAN_WINDOW
    loans = [
        loan
        for loan in Loan.objects.select_related("part", "booking__borrower__contact").filter(
            status__in=(LoanStatus.LENT, LoanStatus.RESERVED)
        )
        if loan.status == LoanStatus.LENT or loan.start <= horizon
    ]
    loans.sort(key=lambda loan: (loan.start, loan.part.name))
    return [
        [
            loan.part.name,
            ", ".join(component.ei for component in loan.part.components.order_by("position")),
            as_text(loan.start),
            as_text(loan.end),
            loan.get_status_display(),
            loan.booking.borrower.display_name,
        ]
        for loan in loans
    ]


def _allocate() -> dict[FormKind, list[int]]:
    """Nächste freie Nummern je Formular; unter einer Sperre, damit keine doppelt vergeben wird."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", [_NUMBER_LOCK])
    numbers = {}
    for kind, count in SHEETS_PER_BINDER.items():
        last = FormSheet.objects.filter(kind=kind).aggregate(last=Max("number"))["last"] or 0
        numbers[kind] = list(range(last + 1, last + count + 1))
    return numbers


def _render(payload: dict[str, Any]) -> bytes:
    result = typst.compile(TEMPLATE, format="pdf", sys_inputs={"data": json.dumps(payload)})
    assert isinstance(result, bytes)  # noqa: S101 – ein Dokument, eine Datei
    return result


def _prune() -> None:
    """Löscht Dateien älterer Mappen; die Einträge mit den Formularnummern bleiben."""
    for binder in Binder.objects.order_by("-created_at", "-id")[KEEP_FILES:]:
        path_of(binder).unlink(missing_ok=True)


def create_binder(*, actor: User | None) -> Binder:
    """Erzeugt und speichert eine neue Notfallmappe. `actor=None`: Verwaltungsbefehl."""
    require(actor, Right.LEND)
    binder = _create(actor)
    _prune()
    return binder


@transaction.atomic
def _create(actor: User | None) -> Binder:
    created_at = timezone.now()
    numbers = _allocate()
    sheets = [
        {"kind": kind.value, "title": kind.label, "label": f"{kind.value}-{number:05d}"}
        for kind, values in numbers.items()
        for number in values
    ]
    payload = {
        "stand": f"{timezone.localtime(created_at):%d.%m.%Y %H:%M}",
        "club": settings.PSAGENT_CLUB_NAME,
        "ranges": [
            [kind.label, f"{kind.value}-{values[0]:05d} bis {kind.value}-{values[-1]:05d}"]
            for kind, values in numbers.items()
        ],
        "parts": _parts(),
        "loans": _loans(),
        "sheets": sheets,
    }
    pdf = _render(payload)
    binder = Binder.objects.create(
        created_at=created_at, created_by=actor, sha256=hashlib.sha256(pdf).hexdigest()
    )
    FormSheet.objects.bulk_create(
        FormSheet(binder=binder, kind=kind, number=number)
        for kind, values in numbers.items()
        for number in values
    )
    path = path_of(binder)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(pdf)
    return binder


def latest_binder() -> Binder | None:
    """Die neueste Mappe, deren Datei noch vorhanden ist."""
    for binder in Binder.objects.order_by("-created_at", "-id")[:KEEP_FILES]:
        if path_of(binder).exists():
            return binder
    return None


def ranges(binder: Binder) -> list[tuple[str, str]]:
    """Formularnummern der Mappe je Formular, z. B. („Sperrung“, „S-00001 bis S-00005“)."""
    result = []
    for kind in FormKind:
        sheets = list(binder.sheets.filter(kind=kind).order_by("number"))
        if sheets:
            result.append((kind.label, f"{sheets[0].label} bis {sheets[-1].label}"))
    return result
