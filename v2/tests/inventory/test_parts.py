"""Teile, Bauteile und ihre Felder (Parität 1 und 2)."""

from datetime import date

import pytest
from django.db import IntegrityError, transaction

from inventory.models import Component, Part, PartKind
from inventory.services import InventoryError, create_product, create_system
from tests.inventory.conftest import MakeProduct, MakeSystem, component


def test_product_has_exactly_one_component(make_product: MakeProduct) -> None:
    """Parität 1: Produkt mit den 9 Feldern des Altsystems."""
    part = make_product("TST-1000")
    stored = part.components.get()

    assert part.kind == PartKind.PRODUCT
    assert stored.position == 1
    assert (stored.year_of_manufacture, stored.purchase_date, stored.first_use_date) == (
        2022,
        date(2023, 3, 1),
        date(2023, 4, 1),
    )


def test_product_name_defaults_to_designation_and_model(make_product: MakeProduct) -> None:
    part = make_product("TST-1001")

    assert part.name == "Klettergurt Testmodell A1"


def test_system_can_have_any_number_of_components(make_system: MakeSystem) -> None:
    """Parität 2: Systeme mit beliebig vielen Bauteilen statt fest drei."""
    part = make_system("TST-1100", "TST-1101", "TST-1102", "TST-1103", "TST-1104")

    assert part.kind == PartKind.SYSTEM
    assert list(part.components.values_list("position", flat=True)) == [1, 2, 3, 4, 5]


def test_system_needs_at_least_one_component(db: None) -> None:
    with pytest.raises(InventoryError):
        create_system("Leeres Set", [])

    assert Part.objects.count() == 0


def test_failed_creation_leaves_nothing_behind(make_product: MakeProduct) -> None:
    make_product("TST-1200")

    with pytest.raises(IntegrityError):
        create_system("Set mit Dublette", [component("TST-1201"), component("tst-1200")])

    assert Part.objects.count() == 1


def test_optional_fields_may_be_empty(db: None) -> None:
    part = create_product(
        component(
            "TST-1300",
            year_of_manufacture=None,
            purchase_date=None,
            first_use_date=None,
            serial_number="",
        )
    )

    assert part.components.get().year_of_manufacture is None


@pytest.mark.parametrize("year", [1899, 2201])
def test_implausible_year_of_manufacture_is_rejected(year: int, db: None) -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        create_product(component("TST-1400", year_of_manufacture=year))


def test_component_positions_are_unique_per_part(make_product: MakeProduct) -> None:
    part = make_product("TST-1500")

    with pytest.raises(IntegrityError), transaction.atomic():
        Component.objects.create(part=part, position=1, **component("TST-1501"))


def test_legacy_reference_is_unique(db: None) -> None:
    create_product(component("TST-1600"), legacy_kind="product", legacy_id=7)

    with pytest.raises(IntegrityError), transaction.atomic():
        create_product(component("TST-1601"), legacy_kind="product", legacy_id=7)

    create_product(component("TST-1602"), legacy_kind="system", legacy_id=7)


def test_legacy_reference_needs_kind_and_id(db: None) -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        Part.objects.create(kind=PartKind.PRODUCT, name="Halb", legacy_id=3)
