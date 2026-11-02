"""Größe als Feld am Bauteil (#124), z. B. für Gurte.

Festlegung (Felix, 31.10. und 02.11.2026): eigenes Feld, nicht Teil der EI; XS–XL, Bereiche
daraus (z. B. M–XL) und Kindergurt 1 und 2; leer heißt „ohne Angabe“. Der Import lässt das Feld
leer. Ändern darf, wer die Beschreibung pflegt (ab Trainer/Materialwart), ohne Sachkunde.
"""

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.test import Client
from django.urls import reverse

from accounts.models import Role
from core import chain
from inventory.labels import entries
from inventory.models import Component, ComponentRecord, Part, RecordKind
from inventory.services import component_problems, update_component
from inventory.sizes import SIZE_CHOICES, covers, size_label, sizes_covering
from tests.helpers import make_inspector
from tests.inventory.conftest import MakeProduct, MakeSystem

pytestmark = pytest.mark.django_db


# --- Werte ---


def test_choices_are_xs_to_xl_ranges_and_kids() -> None:
    values = [value for value, _ in SIZE_CHOICES]

    assert values[:5] == ["XS", "S", "M", "L", "XL"]
    assert "M-XL" in values and "S-M" in values and "XS-XL" in values
    assert "L-S" not in values and "M-M" not in values
    assert values[-2:] == ["K1", "K2"]
    assert len(values) == 5 + 10 + 2


@pytest.mark.parametrize(
    ("size", "label"),
    [("M", "M"), ("M-XL", "M–XL"), ("K1", "Kindergurt 1"), ("", "")],
)
def test_labels(size: str, label: str) -> None:
    assert size_label(size) == label


@pytest.mark.parametrize(
    ("size", "wanted", "result"),
    [
        ("M", "M", True),
        ("M-XL", "L", True),
        ("M-XL", "XL", True),
        ("M-XL", "S", False),
        ("S-M", "M", True),
        ("K1", "K1", True),
        ("K1", "XS", False),
        ("", "M", False),
    ],
)
def test_range_covers_contained_sizes(size: str, wanted: str, result: bool) -> None:
    assert covers(size, wanted) is result


def test_sizes_covering_lists_exact_size_first() -> None:
    assert sizes_covering("L") == ["L", "M-L", "L-XL", "S-L", "M-XL", "XS-L", "S-XL", "XS-XL"]


def test_database_rejects_unknown_size(make_product: MakeProduct) -> None:
    part = make_product()

    with pytest.raises(IntegrityError), transaction.atomic():
        Component.objects.filter(part=part).update(size="L-S")


def test_model_validation_rejects_unknown_size(make_product: MakeProduct) -> None:
    component = make_product().components.get()
    component.size = "XXL"

    with pytest.raises(ValidationError):
        component.full_clean()


# --- Ändern und festhalten ---


def test_trainer_changes_size_without_qualification(make_product: MakeProduct) -> None:
    component = make_product().components.get()
    trainer = make_inspector(role=Role.TRAINER, qualified=False)

    update_component(component, actor=trainer, size="M-XL")

    component.refresh_from_db()
    assert component.size == "M-XL"
    record = component.records.order_by("-id").first()
    assert record is not None
    assert record.kind == RecordKind.CHANGED and record.changed == ["size"]
    assert record.values["size"] == "M-XL"
    assert component_problems() == []
    assert chain.verify() == []


def test_lender_may_not_change_size(make_product: MakeProduct) -> None:
    component = make_product().components.get()

    with pytest.raises(PermissionDenied):
        update_component(component, actor=make_inspector(role=Role.LENDER), size="M")


def test_new_component_starts_without_size(make_product: MakeProduct) -> None:
    component = make_product().components.get()

    assert component.size == ""
    assert component.records.get().values["size"] == ""


def test_records_from_before_the_field_still_match(make_product: MakeProduct) -> None:
    """Ältere Stände kennen das Feld nicht; fehlend heißt leer, kein Manipulationsbefund."""
    component = make_product().components.get()
    latest = component.records.get()
    old_values = {name: value for name, value in latest.values.items() if name != "size"}
    ComponentRecord.objects.create(
        component=component, kind=RecordKind.CHANGED, changed=[], values=old_values
    )

    assert component_problems() == []
    update_component(component, actor=make_inspector(role=Role.TRAINER), size="S")
    assert component_problems() == []


def test_size_changed_behind_the_app_is_found(make_product: MakeProduct) -> None:
    component = make_product().components.get()
    Component.objects.filter(pk=component.pk).update(size="XL")

    [problem] = component_problems()
    assert "Größe" in problem


# --- Anzeige ---


def test_part_page_shows_size(client: Client, make_product: MakeProduct) -> None:
    part = make_product(size="M-XL")
    client.force_login(make_inspector(role=Role.VIEWER, qualified=False))

    page = client.get(reverse("inventory:part_detail", args=[part.pk])).content.decode()

    assert "<th>Größe</th>" in page and "M–XL" in page


def test_part_list_filters_by_size(client: Client, make_product: MakeProduct) -> None:
    exact = make_product(designation="Gurt genau", size="L")
    wide = make_product(designation="Gurt weit", size="M-XL")
    small = make_product(designation="Gurt klein", size="S")
    client.force_login(make_inspector(role=Role.VIEWER, qualified=False))

    page = client.get(reverse("inventory:part_list"), {"groesse": "L"})

    rows = [row["part"] for row in page.context["rows"]]
    assert set(rows) == {exact, wide}
    assert small not in rows


def test_label_names_the_size(make_product: MakeProduct, make_system: MakeSystem) -> None:
    product = make_product(designation="Klettergurt", size="K2")
    system: Part = make_system()
    system.components.filter(position=1).update(size="S-M")

    [label] = entries([product])
    assert label["detail"] == "Größe Kindergurt 2"
    first = entries([system])[0]
    assert first["detail"].endswith("Größe S–M")


def test_edit_form_offers_the_sizes(client: Client, make_product: MakeProduct) -> None:
    part = make_product()
    client.force_login(make_inspector(role=Role.TRAINER, qualified=False))

    page = client.get(reverse("inventory:part_edit", args=[part.pk])).content.decode()

    assert ">Kindergurt 1</option>" in page and ">M–XL</option>" in page
