"""Rechte bei den Stammdaten, geprüft im Dienst (#12): verbotene Fälle ändern nichts.

PSA-relevante Felder, Entsperren, Aussondern und „keine PSA“ prüft
`tests/inspections/test_qualification_required.py`.
"""

from collections.abc import Callable
from typing import Any

import pytest
from django.core.exceptions import PermissionDenied

from accounts.models import Role, User
from inventory.models import Component, Document, EventKind, Part, PartEvent
from inventory.services import (
    create_product,
    create_system,
    link_document,
    record_event,
    store_document_file,
    update_component,
)
from tests.helpers import make_inspector
from tests.inventory.conftest import MakeProduct, component

pytestmark = pytest.mark.django_db

Action = Callable[[User], object]
ACTIONS: dict[str, Action] = {
    "Produkt anlegen": lambda actor: create_product(component("TST-R1"), actor=actor),
    "System anlegen": lambda actor: create_system("Gurt-Set R", [component("TST-R2")], actor=actor),
    "GAL-Datei": lambda actor: store_document_file(
        b"%PDF-1.4 Test", "gal.pdf", title="GAL", actor=actor
    ),
    "GAL-Link": lambda actor: link_document(
        "https://example.org/gal.pdf", title="GAL", actor=actor
    ),
}


def snapshot() -> tuple[Any, ...]:
    return (
        Part.objects.count(),
        Component.objects.count(),
        Document.objects.count(),
        list(PartEvent.objects.values_list("pk", flat=True)),
        list(Component.objects.order_by("pk").values_list("manufacturer", "serial_number")),
    )


def user(role: Role) -> User:
    return make_inspector(role=role, qualified=False)


@pytest.mark.parametrize("role", [Role.LENDER, Role.VIEWER])
@pytest.mark.parametrize("action", list(ACTIONS))
def test_parts_are_managed_from_trainer_up(action: str, role: Role) -> None:
    """Verbotener Fall: Verleiher und Viewer legen keine Teile an und pflegen keine GAL."""
    before = snapshot()

    with pytest.raises(PermissionDenied, match="ab der Rolle Trainer/Materialwart"):
        ACTIONS[action](user(role))

    assert snapshot() == before


@pytest.mark.parametrize("role", [Role.TRAINER, Role.PPE_EXPERT, Role.ADMIN])
@pytest.mark.parametrize("action", list(ACTIONS))
def test_trainer_and_above_manage_parts(action: str, role: Role) -> None:
    ACTIONS[action](user(role))


@pytest.mark.parametrize("role", [Role.LENDER, Role.VIEWER])
def test_description_is_edited_from_trainer_up(role: Role, make_product: MakeProduct) -> None:
    item = make_product().components.get()
    before = snapshot()

    with pytest.raises(PermissionDenied):
        update_component(item, actor=user(role), manufacturer="Beispiel Alpin")

    assert snapshot() == before


def test_expert_without_qualification_edits_description_only(make_product: MakeProduct) -> None:
    """Beschreibung ab Trainer; kommt ein PSA-Feld dazu, braucht es die Sachkunde."""
    expert = user(Role.PPE_EXPERT)
    item = make_product().components.get()

    update_component(item, actor=expert, manufacturer="Beispiel Alpin")
    before = snapshot()
    with pytest.raises(PermissionDenied, match="Sachkunde"):
        update_component(item, actor=expert, manufacturer="Beispiel Nord", year_of_manufacture=2020)

    assert snapshot() == before


def test_viewer_may_not_lock(make_product: MakeProduct) -> None:
    part = make_product()
    before = snapshot()

    with pytest.raises(PermissionDenied, match="ab der Rolle Verleiher"):
        record_event(part, EventKind.LOCKED, actor=user(Role.VIEWER), comment="Naht offen")

    assert snapshot() == before
    assert not part.is_locked()


@pytest.mark.parametrize("role", [Role.LENDER, Role.TRAINER, Role.PPE_EXPERT, Role.ADMIN])
def test_lender_and_above_lock_without_qualification(role: Role, make_product: MakeProduct) -> None:
    """Sperren: jede Rolle ab Verleiher, jederzeit, ohne Sachkunde."""
    part = make_product()

    record_event(part, EventKind.LOCKED, actor=user(role), comment="Naht offen")

    assert part.is_locked()
