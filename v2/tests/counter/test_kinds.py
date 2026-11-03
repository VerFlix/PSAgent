"""Arten an der Theke: Kürzel aus der EI, Präfix als Einstellung (#127)."""

import pytest
from django.test import override_settings

from counter import kinds
from tests.counter.conftest import MakePart

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize(
    ("ei", "code"),
    [
        ("DAV-HL-G-143", "G"),
        ("dav-hl-ks-8", "KS"),
        (" DAV-HL-H-31-B ", "H"),
        ("DAV-HL-LVS-4", "LVS"),
        ("DAV-HL-G-", ""),
        ("DAV-HL-ABCD-1", ""),
        ("TST-L0001", ""),
        ("DAV-XY-G-1", ""),
    ],
)
def test_code_comes_from_the_ei(ei: str, code: str) -> None:
    assert kinds.code_of(ei) == code


@override_settings(PSAGENT_EI_PREFIX="SEK-")
def test_prefix_is_a_setting() -> None:
    assert kinds.code_of("SEK-G-1") == "G"
    assert kinds.code_of("DAV-HL-G-1") == ""


def test_numbers_sort_as_numbers() -> None:
    eis = ["DAV-HL-G-10", "DAV-HL-G-9", "DAV-HL-G-143", "DAV-HL-G-9-B"]

    assert sorted(eis, key=kinds.sort_key) == [
        "DAV-HL-G-9",
        "DAV-HL-G-9-B",
        "DAV-HL-G-10",
        "DAV-HL-G-143",
    ]


def test_quick_access_is_belt_via_ferrata_set_and_helmet() -> None:
    """Festlegung (Felix, 03.11.2026): Schnellzugriff G, KS, H; Gurte nach Größe."""
    assert [kind.code for kind in kinds.quick_kinds()] == ["G", "KS", "H"]
    assert [kind.code for kind in kinds.KINDS if kind.sized] == ["G"]
    assert {kind.code for kind in kinds.KINDS} >= {"S", "EX", "LVS"}


def test_parts_of_a_kind(make_part: MakePart) -> None:
    belt = make_part("DAV-HL-G-1")
    make_part("DAV-HL-GX-1", designation="Sonstiges")
    make_part("DAV-HL-H-1", designation="Helm")

    belt_kind = kinds.by_code("G")
    assert belt_kind is not None
    assert list(kinds.parts_of(belt_kind)) == [belt]
