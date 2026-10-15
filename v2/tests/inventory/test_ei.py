"""Die EI ist über alle Bauteile eindeutig, ohne Groß-/Kleinschreibung und Leerzeichen.

Die Datenbank erzwingt das selbst; die Tests schreiben deshalb auch an den Anlege-Funktionen
vorbei direkt über das ORM (CLAUDE.md, Fachregeln).
"""

import pytest
from django.db import IntegrityError, transaction

from inventory.models import Component, EventKind, Part, PartKind, normalize_ei
from inventory.services import record_event
from tests.inventory.conftest import MakeProduct, MakeSystem, component


@pytest.mark.parametrize(
    "duplicate",
    ["AB12", "ab12", "AB 12", "ab 12", " AB12 ", "A B 1 2", "ab\t12", "Ab12\n"],
)
def test_duplicate_ei_is_rejected_by_database(duplicate: str, make_product: MakeProduct) -> None:
    make_product("AB12")
    other = Part.objects.create(kind=PartKind.PRODUCT, name="Zweites Teil")

    with pytest.raises(IntegrityError), transaction.atomic():
        Component.objects.create(part=other, position=1, **component(duplicate))


def test_duplicate_ei_between_product_and_system(
    make_product: MakeProduct, make_system: MakeSystem
) -> None:
    make_product("TST-0100")

    with pytest.raises(IntegrityError), transaction.atomic():
        make_system("TST-0101", " Tst-0100 ")


def test_duplicate_ei_within_one_system(make_system: MakeSystem) -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        make_system("TST-0200", "TST-0200")


def test_ei_of_retired_part_stays_reserved(make_product: MakeProduct) -> None:
    part = make_product("TST-0300")
    record_event(part, EventKind.RETIRED, actor=None, comment="Sturz, ausgesondert")

    with pytest.raises(IntegrityError), transaction.atomic():
        make_product("TST-0300")


@pytest.mark.parametrize("ei", ["", "   ", "\t"])
def test_every_component_needs_an_ei(ei: str, make_product: MakeProduct) -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        make_product(ei)


def test_different_eis_are_accepted(make_product: MakeProduct) -> None:
    make_product("TST-0400")
    make_product("TST-0401")

    assert Component.objects.count() == 2


def test_ei_key_matches_python_normalization(make_product: MakeProduct) -> None:
    part = make_product(" Ab 12-x ")
    stored = part.components.get()
    stored.refresh_from_db()

    assert stored.ei == " Ab 12-x "
    assert stored.ei_key == normalize_ei(" Ab 12-x ") == "ab12-x"
    assert Component.objects.get(ei_key=normalize_ei("AB12-X")) == stored
