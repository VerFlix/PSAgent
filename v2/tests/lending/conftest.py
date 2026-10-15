"""Hilfen für die Tests des Verleihs. Nur künstliche Testdaten."""

import itertools
from collections.abc import Callable
from datetime import date, timedelta
from typing import Any

import pytest

from accounts.models import Role, User
from inventory.models import Part
from inventory.services import create_product, link_document
from lending.models import Borrower
from lending.services import create_borrower
from tests.helpers import adopt_inspection, make_inspector

TODAY = date(2026, 10, 15)
DAY = timedelta(days=1)
NEXT_DUE = TODAY + 365 * DAY
STROKES = [[[10, 10], [20, 15], [30, 12]], [[12, 30], [28, 32]]]

_numbers = itertools.count(1)

MakePart = Callable[..., Part]


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fester Stichtag, damit „überfällig“ nicht vom Kalender abhängt."""
    monkeypatch.setattr("lending.services.today", lambda: TODAY)


@pytest.fixture
def lender(db: None) -> User:
    return User.objects.create_user(
        username="verleih", first_name="Testperson", last_name="Muster", role=Role.LENDER
    )


@pytest.fixture
def inspector(db: None) -> User:
    return make_inspector()


@pytest.fixture
def borrower(db: None) -> Borrower:
    return create_borrower(
        "Kim Beispiel",
        address="Teststraße 1, 00000 Beispielstadt",
        email="kim.beispiel@example.org",
    )


@pytest.fixture
def make_part(db: None) -> MakePart:
    """Freigegebenes, geprüftes Teil mit GAL-Link; Status über Parameter änderbar."""

    def make(
        *, released: bool = True, gal: bool = True, next_due: date = NEXT_DUE, **fields: Any
    ) -> Part:
        number = next(_numbers)
        part = create_product({"designation": "Klettergurt", "ei": f"TST-L{number:04d}", **fields})
        if gal:
            part.documents.add(
                link_document(f"https://example.org/gal/{number}.pdf", title="GAL Testmodell")
            )
        if released:
            adopt_inspection([part], next_due=next_due)
        return part

    return make


def checkout_kwargs(actor: User) -> dict[str, Any]:
    """Pflichtangaben einer Ausgabe."""
    return {
        "actor": actor,
        "quick_check": True,
        "gal_provided": True,
        "signature": STROKES,
        "signer_name": "Kim Beispiel",
    }


def return_kwargs(actor: User) -> dict[str, Any]:
    """Pflichtangaben einer Rücknahme."""
    return {
        "actor": actor,
        "quick_check": True,
        "signature": STROKES,
        "signer_name": "Kim Beispiel",
    }
