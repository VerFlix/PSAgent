"""Hilfen für die Tests der PSA-Prüfung. Nur künstliche Testdaten."""

import itertools
from collections.abc import Callable
from datetime import date, timedelta
from typing import Any

import pytest
from django.utils import timezone

from accounts.models import User
from inventory.models import Part
from inventory.services import create_product
from tests.helpers import make_inspector

_numbers = itertools.count(1)

MakePart = Callable[..., Part]


def in_days(days: int) -> date:
    """Datum relativ zu heute (Prüfregeln vergleichen mit dem echten Datum)."""
    return timezone.localdate() + timedelta(days=days)


@pytest.fixture
def inspector(db: None) -> User:
    return make_inspector()


@pytest.fixture
def make_part(db: None) -> MakePart:
    """Neues, noch nicht freigegebenes Teil."""

    def make(**fields: Any) -> Part:
        number = next(_numbers)
        return create_product({"designation": "Bandschlinge", "ei": f"TST-P{number:04d}", **fields})

    return make
