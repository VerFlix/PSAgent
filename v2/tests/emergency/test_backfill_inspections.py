"""Prüfprotokolle aus dem Notbetrieb nacherfassen (#80, Parität 22).

Festlegung (Felix, 02.11.2026):

1. Entsperren, Aussondern und Freigabe wirken mit dem Datum vom Formular. Gibt es am Teil danach
   schon Sperr- oder Prüfvorgänge, wird nichts übernommen; der Fall kommt auf die Klärungsliste.
2. Die Sachkunde des Prüfers vom Papier galt am Prüfdatum; wer signiert, braucht sie beim
   Signieren.
3. Wer nacherfasst, signiert die Übereinstimmung mit dem Papier. Der PSA-Prüfer erfasst seine
   eigenen Protokolle, der Admin mit Sachkunde alle; der Name vom Papier steht dabei.
4. Eine Prüfungsrunde je Formular P, normal geschlossen und mit dem Signier-Tool signiert.
"""

import io
import re
from datetime import date, timedelta
from typing import Any

import pytest
from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from pypdf import PdfReader

from accounts.models import Qualification, Role, User
from core import chain
from emergency import backfill
from emergency.models import InspectionForm, PaperForm
from inspections.models import (
    InspectionRound,
    Outcome,
    RoundProtocol,
    RoundState,
    SkippedEffect,
)
from inspections.services import next_inspection_due
from inventory.models import EventKind, Part, PartStatus
from inventory.services import record_event
from tests.emergency.test_backfill import binder_printed
from tests.helpers import make_inspector, signed_inspection
from tests.lending.conftest import MakePart
from tests.signing import sign

pytestmark = pytest.mark.django_db
SCAN = b"%PDF-1.4\n% Scan Pruefprotokoll, kuenstliche Testdaten\n%%EOF\n"
TODAY = timezone.localdate()
ON = TODAY - timedelta(days=5)
NEXT = TODAY + timedelta(days=360)


@pytest.fixture
def expert() -> User:
    return make_inspector()


@pytest.fixture(autouse=True)
def printed() -> None:
    binder_printed(days_ago=10)


def fresh(make_part: MakePart) -> Part:
    """Teil ohne frühere Prüfung: Nichts am Teil liegt nach dem Datum vom Formular."""
    return make_part(released=False)


def ei_of(part: Part) -> str:
    return str(part.components.get().ei)


def row(part: Part, outcome: str = Outcome.PASSED, **fields: Any) -> dict[str, Any]:
    next_due = None if outcome == Outcome.RETIRE else NEXT
    return {"ei": ei_of(part), "outcome": outcome, "comment": "", "next_due": next_due, **fields}


def capture(
    actor: User, rows: list[dict[str, Any]], *, number: str = "P-00001", **fields: Any
) -> InspectionForm:
    arguments: dict[str, Any] = {"performed_on": ON, "inspector": actor, **fields}
    return backfill.capture_inspection_form(
        number, [(SCAN, "scan.pdf")], rows=rows, actor=actor, **arguments
    )


def text_of(pdf: bytes) -> str:
    reader = PdfReader(io.BytesIO(pdf))
    return re.sub(r"\s+", " ", " ".join(page.extract_text() for page in reader.pages))


def day_of(moment: Any) -> date:
    return timezone.localtime(moment).date()


# --- Erfassen ---


def test_parity_22_form_becomes_closed_round_with_protocol(
    expert: User, make_part: MakePart
) -> None:
    part = fresh(make_part)

    form = capture(expert, [row(part, comment="Naht in Ordnung")])

    round_ = form.round
    assert round_.inspector == expert and round_.state() == RoundState.CLOSED
    assert day_of(round_.performed_at) == ON
    assert form.paper_form.sheet.label == "P-00001" and form.paper_form.recorded_by == expert
    assert form.inspector == expert and form.inspector_name == expert.get_full_name()
    result = round_.current_results().get()
    assert (result.part, result.outcome, result.next_due) == (part, Outcome.PASSED, NEXT)
    text = text_of(bytes(RoundProtocol.objects.get(round=round_).pdf))
    assert "Formular P-00001" in text
    assert f"geprüft am {ON:%d.%m.%Y}" in text
    assert expert.get_full_name() in text
    assert chain.verify() == []


def test_nothing_changes_before_signing(expert: User, make_part: MakePart) -> None:
    part = fresh(make_part)
    record_event(part, EventKind.LOCKED, actor=None, comment="vorher gesperrt", at=_before())

    capture(expert, [row(part)])

    assert part.is_locked()
    assert next_inspection_due(part) is None


def _before() -> Any:
    return timezone.now() - timedelta(days=8)


# --- Signieren: Wirkung mit dem Datum vom Formular ---


def test_signing_unlocks_with_the_form_date(expert: User, make_part: MakePart) -> None:
    part = fresh(make_part)
    record_event(part, EventKind.LOCKED, actor=None, comment="vorher gesperrt", at=_before())
    form = capture(expert, [row(part)])

    sign(form.round, actor=expert)

    assert not part.is_locked()
    unlocked = part.events.get(kind=EventKind.UNLOCKED)
    assert day_of(unlocked.at) == ON
    assert next_inspection_due(part) == NEXT
    assert not SkippedEffect.objects.exists()
    assert chain.verify() == []


def test_new_part_is_released_with_the_form_date(expert: User, make_part: MakePart) -> None:
    part = make_part(released=False)
    assert part.status() == PartStatus.NEW
    form = capture(expert, [row(part)])

    sign(form.round, actor=expert)

    assert part.status() == PartStatus.RELEASED
    assert day_of(part.events.get(kind=EventKind.RELEASED).at) == ON


def test_retire_with_the_form_date(expert: User, make_part: MakePart) -> None:
    part = fresh(make_part)
    form = capture(expert, [row(part, Outcome.RETIRE, comment="Gurtband aufgescheuert")])

    sign(form.round, actor=expert)

    assert day_of(part.events.get(kind=EventKind.RETIRED).at) == ON


def test_later_lock_keeps_result_out_and_lists_it(expert: User, make_part: MakePart) -> None:
    part = fresh(make_part)
    record_event(part, EventKind.LOCKED, actor=None, comment="nach dem Notbetrieb gesperrt")
    form = capture(expert, [row(part)])

    sign(form.round, actor=expert)

    assert part.is_locked()
    assert not part.events.filter(kind=EventKind.UNLOCKED).exists()
    assert next_inspection_due(part) is None
    skipped = SkippedEffect.objects.get()
    assert skipped.result.part == part
    [case] = [case for case in backfill.open_cases() if case.key.startswith("pruefung:")]
    assert case.key == f"pruefung:{skipped.pk}"
    assert "P-00001" in case.title and str(part) in case.title
    assert "später" in case.detail

    backfill.resolve(case.key, note="Teil neu geprüft", actor=expert)
    assert not [case for case in backfill.open_cases() if case.key.startswith("pruefung:")]


def test_later_inspection_keeps_result_out(expert: User, make_part: MakePart) -> None:
    part = fresh(make_part)
    later = TODAY + timedelta(days=200)
    signed_inspection(make_inspector(), [part], next_due=later)
    form = capture(expert, [row(part)])

    sign(form.round, actor=expert)

    assert next_inspection_due(part) == later
    assert SkippedEffect.objects.get().result.part == part


def test_only_affected_parts_are_skipped(expert: User, make_part: MakePart) -> None:
    calm, busy = fresh(make_part), fresh(make_part)
    record_event(busy, EventKind.LOCKED, actor=None, comment="nach dem Notbetrieb gesperrt")
    form = capture(expert, [row(calm), row(busy)])

    sign(form.round, actor=expert)

    assert next_inspection_due(calm) == NEXT
    assert [skip.result.part for skip in SkippedEffect.objects.all()] == [busy]


# --- Sachkunde und Rechte ---


def test_paper_inspector_needs_qualification_on_the_form_date(make_part: MakePart) -> None:
    newcomer = make_inspector(qualified=False)
    Qualification.objects.create(
        user=newcomer,
        valid_from=TODAY - timedelta(days=2),
        valid_until=TODAY + timedelta(days=365),
        evidence="Lehrgang PSA-Sachkunde (Testdaten)",
    )

    with pytest.raises(backfill.BackfillError, match="keine gültige Sachkunde"):
        capture(newcomer, [row(fresh(make_part))])

    assert (
        not PaperForm.objects.exists()
        and not InspectionRound.objects.filter(performed_at__isnull=False).exists()
    )


def test_expert_captures_only_own_protocols(expert: User, make_part: MakePart) -> None:
    with pytest.raises(PermissionDenied):
        capture(expert, [row(fresh(make_part))], inspector=make_inspector())


def test_admin_captures_and_signs_for_another_expert(expert: User, make_part: MakePart) -> None:
    admin = make_inspector(role=Role.ADMIN)
    part = fresh(make_part)

    form = capture(admin, [row(part)], inspector=expert)

    assert form.round.inspector == admin and form.inspector == expert
    text = text_of(bytes(RoundProtocol.objects.get(round=form.round).pdf))
    assert expert.get_full_name() in text and admin.get_full_name() in text
    sign(form.round, actor=admin)
    assert next_inspection_due(part) == NEXT


@pytest.mark.parametrize("role", [Role.TRAINER, Role.LENDER, Role.VIEWER])
def test_other_roles_may_not_capture(role: Role, expert: User, make_part: MakePart) -> None:
    with pytest.raises(PermissionDenied):
        capture(make_inspector(role=role), [row(fresh(make_part))], inspector=expert)


def test_expert_without_valid_qualification_may_not_capture(make_part: MakePart) -> None:
    lapsed = make_inspector(qualified=False)

    with pytest.raises(PermissionDenied):
        capture(lapsed, [row(fresh(make_part))])


# --- Prüfungen der Eingaben ---


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"number": "A-00001"}, "keine Formularnummer für „Prüfprotokoll“"),
        ({"performed_on": TODAY + timedelta(days=1)}, "in der Zukunft"),
        ({"performed_on": TODAY - timedelta(days=20)}, "vor dem Druck"),
        ({"rows": []}, "mindestens eine Zeile"),
        (
            {"rows": [{"ei": "UNBEKANNT-1", "outcome": Outcome.PASSED, "next_due": NEXT}]},
            "Kein Teil",
        ),
    ],
)
def test_invalid_forms_store_nothing(
    expert: User, make_part: MakePart, change: dict[str, Any], message: str
) -> None:
    rows = change.pop("rows", None)
    if rows is None:
        rows = [row(fresh(make_part))]

    with pytest.raises(backfill.BackfillError, match=message):
        capture(expert, rows, **change)

    assert not PaperForm.objects.exists()


def test_same_part_twice_is_rejected(expert: User, make_part: MakePart) -> None:
    part = fresh(make_part)

    with pytest.raises(backfill.BackfillError, match="zweimal"):
        capture(expert, [row(part), row(part)])


def test_next_due_counts_from_the_form_date(expert: User, make_part: MakePart) -> None:
    part = fresh(make_part)
    capture(expert, [row(part, next_due=TODAY - timedelta(days=1))], number="P-00001")

    with pytest.raises(backfill.BackfillError, match="nach dem Prüfdatum"):
        capture(expert, [row(fresh(make_part), next_due=ON)], number="P-00002")


# --- Oberfläche ---


def test_page_offers_only_own_name_to_an_expert(client: Client, expert: User) -> None:
    client.force_login(expert)

    response = client.get(reverse("emergency:backfill_inspections"))

    assert response.status_code == 200
    page = response.content.decode()
    assert expert.get_full_name() in page
    other = make_inspector()
    assert (
        other.get_full_name()
        not in client.get(reverse("emergency:backfill_inspections")).content.decode()
    )


def test_page_captures_and_leads_to_signing(
    client: Client, expert: User, make_part: MakePart
) -> None:
    part = fresh(make_part)
    client.force_login(expert)
    data = {
        "number": "P-00001",
        "performed_on": ON.isoformat(),
        "inspector": str(expert.pk),
        "rows-TOTAL_FORMS": "2",
        "rows-INITIAL_FORMS": "0",
        "rows-1-ei": "",
        "rows-1-outcome": "",
        "rows-1-comment": "",
        "rows-1-next_due": "",
        "rows-0-ei": ei_of(part),
        "rows-0-outcome": Outcome.PASSED,
        "rows-0-comment": "",
        "rows-0-next_due": NEXT.isoformat(),
        "scan": SimpleUploadedFile("scan.pdf", SCAN, content_type="application/pdf"),
    }

    response = client.post(reverse("emergency:backfill_inspections"), data)

    assert response.status_code == 302
    assert response["Location"] == reverse("inspections:round_mode")
    assert InspectionForm.objects.get().round.state() == RoundState.CLOSED


def test_emergency_page_links_for_experts_only(client: Client, expert: User) -> None:
    client.force_login(expert)
    assert (
        reverse("emergency:backfill_inspections")
        in client.get(reverse("emergency:index")).content.decode()
    )

    client.force_login(make_inspector(role=Role.LENDER))
    assert (
        reverse("emergency:backfill_inspections")
        not in client.get(reverse("emergency:index")).content.decode()
    )
