"""Hilfen für die Tests der Theke (#127). Nur künstliche Testdaten."""

from collections.abc import Callable
from datetime import date, timedelta
from typing import Any

import pytest
from django.test import Client

from accounts.models import Role, User
from inventory.models import Part
from inventory.services import create_product, link_document
from lending.models import Borrower
from lending.services import create_borrower
from tests.helpers import adopt_inspection

TODAY = date(2026, 10, 15)
DAY = timedelta(days=1)
NEXT_DUE = TODAY + 365 * DAY
STROKES = [[[10, 10], [20, 15], [30, 12]], [[12, 30], [28, 32]]]

MakePart = Callable[..., Part]


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fester Stichtag, damit Dauern und Prüftermine nicht vom Kalender abhängen."""
    monkeypatch.setattr("lending.services.today", lambda: TODAY)


@pytest.fixture
def lender(db: None) -> User:
    return User.objects.create_user(
        username="theke", first_name="Testperson", last_name="Theke", role=Role.LENDER
    )


@pytest.fixture
def client_lender(client: Client, lender: User) -> Client:
    client.force_login(lender)
    return client


@pytest.fixture
def borrower(db: None) -> Borrower:
    return create_borrower(
        "Kim Beispiel", address="Teststraße 1, 00000 Beispielstadt", email="kim@example.org"
    )


@pytest.fixture
def make_part(db: None) -> MakePart:
    """Freigegebenes, geprüftes Teil mit GAL und EI im Schema des Vereins, z. B. DAV-HL-G-1."""

    def make(
        ei: str,
        *,
        designation: str = "Klettergurt",
        size: str = "",
        next_due: date = NEXT_DUE,
        **fields: Any,
    ) -> Part:
        part = create_product({"designation": designation, "ei": ei, "size": size, **fields})
        part.documents.add(link_document(f"https://example.org/gal/{ei}.pdf", title=f"GAL {ei}"))
        adopt_inspection([part], next_due=next_due)
        return part

    return make
