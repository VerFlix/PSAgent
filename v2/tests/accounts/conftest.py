"""Hilfen für die Tests von Benutzern, Rollen und Sachkunde. Nur künstliche Testdaten."""

import itertools
from collections.abc import Callable
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from accounts.models import Qualification, Role, User

BERLIN = ZoneInfo("Europe/Berlin")
# Fester Zeitpunkt, damit Tests nicht vom heutigen Datum abhängen.
NOW = datetime(2026, 10, 15, 12, 0, tzinfo=BERLIN)
TODAY = NOW.date()

_numbers = itertools.count(1)

MakeUser = Callable[..., User]
Grant = Callable[..., Qualification]


@pytest.fixture
def make_user(db: None) -> MakeUser:
    """Legt ein Konto mit künstlichem Namen an."""

    def make(role: Role = Role.VIEWER, **fields: Any) -> User:
        number = next(_numbers)
        return User.objects.create_user(
            username=f"test{number}",
            first_name="Testperson",
            last_name=f"Beispiel {number}",
            role=role,
            **fields,
        )

    return make


@pytest.fixture
def grant(db: None) -> Grant:
    """Trägt eine Sachkunde ein."""

    def grant_qualification(user: User, valid_from: date, valid_until: date) -> Qualification:
        return Qualification.objects.create(
            user=user,
            valid_from=valid_from,
            valid_until=valid_until,
            evidence="Lehrgang PSA-Sachkunde (Testdaten)",
        )

    return grant_qualification
