"""Festgehaltener Stand der Bauteile (#20): angelegt und jede Änderung, nur anhängbar, verkettet.

Hintergrund: EI, Herstellungsjahr und Datum 1. Einsatz dürfen nur Sachkundige ändern. Bisher
wurde das Bauteil überschrieben, ohne Spur. Jetzt hält `ComponentRecord` den Stand nach jedem
Anlegen und Ändern fest; das Prüfwerkzeug vergleicht ihn mit dem Bauteil
(`inventory.services.component_problems`). So fällt eine Änderung an der App vorbei auf.
"""

import json
from datetime import date

import pytest
from django.db import connection

from accounts.models import User
from core import chain
from core.append_only import AppendOnlyError
from inventory.models import Component, ComponentRecord, RecordKind
from inventory.services import (
    InventoryError,
    component_problems,
    component_values,
    update_component,
)
from tests.helpers import tamper
from tests.inventory.conftest import MakeProduct, MakeSystem, component

pytestmark = pytest.mark.django_db


def test_creating_records_the_full_state(make_product: MakeProduct) -> None:
    part = make_product("TST-REC1")
    stored = part.components.get()

    [record] = stored.records.all()

    assert record.kind == RecordKind.CREATED
    assert record.values == component_values(stored)
    assert record.values["ei"] == "TST-REC1"
    assert record.values["year_of_manufacture"] == "2022"
    assert record.values["first_use_date"] == "2023-04-01"
    assert component_problems() == []


def test_each_component_of_a_system_is_recorded(make_system: MakeSystem) -> None:
    part = make_system("TST-REC2", "TST-REC3")

    assert [c.records.count() for c in part.components.all()] == [1, 1]


def test_changes_are_recorded_with_actor_and_fields(make_product: MakeProduct, actor: User) -> None:
    stored = make_product("TST-REC4").components.get()

    update_component(stored, actor=actor, year_of_manufacture=2020, serial_number="SN-NEU")

    record = stored.records.order_by("-id").first()
    assert record is not None
    assert record.kind == RecordKind.CHANGED
    assert record.actor == actor
    assert sorted(record.changed) == ["serial_number", "year_of_manufacture"]
    assert record.values["year_of_manufacture"] == "2020"
    assert component_problems() == []


def test_unchanged_update_records_nothing(make_product: MakeProduct, actor: User) -> None:
    stored = make_product("TST-REC5").components.get()

    update_component(stored, actor=actor, year_of_manufacture=2022)

    assert stored.records.count() == 1


def test_records_are_append_only(make_product: MakeProduct) -> None:
    record = make_product("TST-REC6").components.get().records.get()

    with pytest.raises(AppendOnlyError):
        ComponentRecord.objects.filter(pk=record.pk).update(values={})
    with pytest.raises(AppendOnlyError):
        record.delete()


# --- an der App vorbei ---


def test_field_changed_in_database_is_found(make_product: MakeProduct) -> None:
    stored = make_product("TST-REC7").components.get()

    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE inventory_component SET year_of_manufacture = 2025 WHERE id = %s",
            [stored.pk],
        )

    assert component_problems() == [
        f"Bauteil #{stored.pk} (Teil #{stored.part_id}): Herstellungsjahr weicht vom "
        "festgehaltenen Stand ab (an der App vorbei geändert)."
    ]


def test_component_moved_to_another_part_is_found(make_product: MakeProduct) -> None:
    stored = make_product("TST-REC8").components.get()
    other = make_product("TST-REC9")

    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE inventory_component SET part_id = %s, position = 2 WHERE id = %s",
            [other.pk, stored.pk],
        )

    where = f"Bauteil #{stored.pk} (Teil #{other.pk})"
    suffix = "weicht vom festgehaltenen Stand ab (an der App vorbei geändert)."
    assert component_problems() == [f"{where}: Teil {suffix}", f"{where}: Position {suffix}"]


def test_component_inserted_in_database_is_found(make_product: MakeProduct) -> None:
    part = make_product("TST-REC10")
    fields = component("TST-REC11")
    inserted = Component(part=part, position=2, **fields)
    # Wie an der App vorbei: ohne Dienst, also ohne festgehaltenen Stand.
    Component.objects.bulk_create([inserted])
    stored = Component.objects.get(ei="TST-REC11")

    assert component_problems() == [
        f"Bauteil #{stored.pk} (Teil #{part.pk}): kein festgehaltener Stand "
        "(an der App vorbei angelegt)."
    ]


def test_record_rewritten_in_database_is_found_by_the_chain(make_product: MakeProduct) -> None:
    """Wer Bauteil und Stand passend ändert, fällt an der Hash-Kette auf."""
    stored = make_product("TST-REC12").components.get()
    record = stored.records.get()
    values = {**record.values, "year_of_manufacture": "2025"}
    tamper(
        "inventory_componentrecord",
        "UPDATE inventory_componentrecord SET values = %s WHERE id = %s",
        [json.dumps(values), record.pk],
    )
    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE inventory_component SET year_of_manufacture = 2025 WHERE id = %s",
            [stored.pk],
        )

    assert component_problems() == []
    assert any(
        f"(inventory.ComponentRecord #{record.pk}): Vorgang wurde geändert." in problem
        for problem in chain.verify()
    )


def test_values_are_text_and_empty_for_missing(make_product: MakeProduct) -> None:
    stored = make_product("TST-REC13", purchase_date=None, serial_number="").components.get()

    values = component_values(stored)

    assert values["purchase_date"] == ""
    assert values["serial_number"] == ""
    assert values["part"] == str(stored.part_id) and values["position"] == "1"
    assert all(isinstance(value, str) for value in values.values())
    assert date.fromisoformat(values["first_use_date"]) == stored.first_use_date


# --- Verdecken durch spätere Änderungen (Sicherheitsprüfung zu #109) ---


def test_tampered_component_is_not_laundered_by_a_later_edit(
    make_product: MakeProduct, actor: User
) -> None:
    """Manipulation in der Datenbank, danach eine erlaubte Änderung in der App: abgelehnt."""
    stored = make_product("TST-REC14").components.get()
    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE inventory_component SET year_of_manufacture = 2025 WHERE id = %s",
            [stored.pk],
        )

    with pytest.raises(InventoryError, match="passt nicht zum festgehaltenen Stand"):
        update_component(stored, actor=actor, serial_number="SN-NEU")

    assert stored.records.count() == 1
    assert len(component_problems()) == 1


def test_unlisted_change_between_records_is_found(make_product: MakeProduct) -> None:
    """Ein späterer Stand darf sich nur in den vermerkten Feldern unterscheiden."""
    stored = make_product("TST-REC15").components.get()
    first = stored.records.get()
    later = ComponentRecord.objects.create(
        component=stored,
        kind=RecordKind.CHANGED,
        changed=["serial_number"],
        values={**first.values, "serial_number": "SN-2", "year_of_manufacture": "2025"},
    )
    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE inventory_component SET year_of_manufacture = 2025, serial_number = 'SN-2'"
            " WHERE id = %s",
            [stored.pk],
        )

    assert component_problems() == [
        f"Bauteil #{stored.pk} (Teil #{stored.part_id}): Herstellungsjahr hat sich bis "
        f"Stand #{later.pk} ohne Vermerk geändert."
    ]


def test_history_must_start_with_creation(make_product: MakeProduct) -> None:
    part = make_product("TST-REC16")
    Component.objects.bulk_create([Component(part=part, position=2, **component("TST-REC17"))])
    inserted = Component.objects.get(ei="TST-REC17")
    ComponentRecord.objects.create(
        component=inserted,
        kind=RecordKind.CHANGED,
        changed=["serial_number"],
        values=component_values(inserted),
    )

    assert component_problems() == [
        f"Bauteil #{inserted.pk} (Teil #{part.pk}): Der erste festgehaltene Stand ist keine Anlage."
    ]
