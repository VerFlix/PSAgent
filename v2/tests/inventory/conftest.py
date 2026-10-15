"""Hilfen für die Tests der Stammdaten. Nur künstliche Testdaten."""

import itertools
from collections.abc import Callable, Iterator
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from django.test import override_settings

from accounts.models import Role, User
from inventory.models import Part
from inventory.services import create_product, create_system

_numbers = itertools.count(1)

MakeProduct = Callable[..., Part]
MakeSystem = Callable[..., Part]


def component(ei: str, **fields: Any) -> dict[str, Any]:
    """Felder eines künstlichen Bauteils."""
    return {
        "designation": "Klettergurt",
        "standard": "EN 12277",
        "model_name": "Testmodell A1",
        "manufacturer": "Beispiel Bergsport",
        "year_of_manufacture": 2022,
        "purchase_date": date(2023, 3, 1),
        "first_use_date": date(2023, 4, 1),
        "ei": ei,
        "serial_number": "SN-TEST-1",
        **fields,
    }


@pytest.fixture
def actor(db: None) -> User:
    return User.objects.create_user(
        username="pruefer", first_name="Testperson", last_name="Probe", role=Role.PPE_EXPERT
    )


@pytest.fixture
def make_product(db: None) -> MakeProduct:
    def make(ei: str | None = None, **fields: Any) -> Part:
        return create_product(
            component(ei if ei is not None else f"TST-{next(_numbers):04d}", **fields)
        )

    return make


@pytest.fixture
def make_system(db: None) -> MakeSystem:
    def make(*eis: str) -> Part:
        number = next(_numbers)
        eis = eis or (f"TST-S{number}-1", f"TST-S{number}-2")
        return create_system(f"Gurt-Set {number}", [component(ei) for ei in eis])

    return make


@pytest.fixture(autouse=True)
def media_root(tmp_path: Path) -> Iterator[Path]:
    """Dateien landen im Testordner, nie im Repo."""
    root = tmp_path / "media"
    with override_settings(MEDIA_ROOT=root):
        yield root
