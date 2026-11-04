"""„Heute“ an der Theke (#127, Teil 3): Abholungen, Rückgaben, Bestand je Größe.

Festlegung (Felix, 03.11.2026): Bestand der Arten im Schnellzugriff; bei „nach Größe“ je Größe
heute frei, verliehen und gesperrt. Ein verstellbarer Gurt zählt bei jeder abgedeckten Größe.
"""

import pytest
from django.test import Client
from django.urls import reverse

from accounts.models import Role, User
from counter import kinds
from counter.today import stock_rows
from inventory.models import EventKind
from inventory.services import record_event
from lending.models import Borrower
from lending.services import create_borrower, lend, reserve
from tests.counter.conftest import DAY, STROKES, TODAY, MakePart
from tests.helpers import make_inspector

pytestmark = pytest.mark.django_db
URL = reverse("counter:today")


def out(borrower: Borrower, parts: list, start_days: int, days: int) -> None:  # type: ignore[type-arg]
    start = TODAY + start_days * DAY
    lend(
        borrower,
        parts,
        start,
        start + days * DAY,
        actor=None,
        quick_check=True,
        gal_provided=True,
        signature=STROKES,
        signer_name="Kim Beispiel",
    )


def test_pickups_and_returns_of_today(
    client_lender: Client, make_part: MakePart, borrower: Borrower, lender: User
) -> None:
    later = create_borrower("Noa Später", address="Weg 2", email="noa@example.org")
    late = create_borrower("Erika Spät", address="Weg 3", email="erika@example.org")
    reserve(borrower, [make_part("DAV-HL-H-1", designation="Helm")], TODAY, TODAY, actor=lender)
    reserve(
        later, [make_part("DAV-HL-H-2", designation="Helm")], TODAY + DAY, TODAY + DAY, actor=lender
    )
    out(late, [make_part("DAV-HL-H-3", designation="Helm")], -4, 2)
    out(borrower, [make_part("DAV-HL-H-4", designation="Helm")], 0, 0)
    out(later, [make_part("DAV-HL-H-5", designation="Helm")], 0, 5)

    page = client_lender.get(URL).content.decode()

    pickups, returns = page.split('id="zurueck"')
    assert "Kim Beispiel" in pickups and "ab heute" in pickups and "Noa Später" not in pickups
    assert returns.index("Erika Spät") < returns.index("Kim Beispiel")
    assert "2 Tage überfällig" in returns and "fällig heute" in returns
    assert "Noa Später" not in returns.split('id="bestand"')[0]


def test_stock_per_size_counts_free_lent_and_locked(
    make_part: MakePart, borrower: Borrower, lender: User
) -> None:
    make_part("DAV-HL-G-1", size="M")
    make_part("DAV-HL-G-2", size="M-XL")
    lent = make_part("DAV-HL-G-3", size="M")
    out(borrower, [lent], 0, 7)
    locked = make_part("DAV-HL-G-4", size="L")
    record_event(locked, EventKind.LOCKED, actor=lender, comment="Naht offen")
    make_part("DAV-HL-G-5", size="")
    make_part("DAV-HL-G-6", size="S", next_due=TODAY - DAY)  # PSA-Prüfung fällig: nicht frei
    belt = kinds.by_code("G")
    assert belt is not None

    rows = {row.label: (row.free, row.lent, row.locked) for row in stock_rows(belt)}

    assert rows["M"] == (2, 1, 0)  # G-1 genau, G-2 verstellbar; G-3 verliehen
    assert rows["L"] == (1, 0, 1)  # G-2 verstellbar; G-4 gesperrt
    assert rows["S"] == (0, 0, 0)  # G-6 ist weder frei noch verliehen noch gesperrt
    assert rows["ohne"] == (1, 0, 0)


def test_stock_without_sizes_is_one_row(make_part: MakePart, borrower: Borrower) -> None:
    make_part("DAV-HL-H-1", designation="Helm")
    lent = make_part("DAV-HL-H-2", designation="Helm")
    out(borrower, [lent], 0, 3)
    helmet = kinds.by_code("H")
    assert helmet is not None

    (row,) = stock_rows(helmet)

    assert (row.label, row.free, row.lent, row.locked) == ("Helm", 1, 1, 0)


def test_today_page_shows_the_stock(client_lender: Client, make_part: MakePart) -> None:
    make_part("DAV-HL-G-1", size="M")

    page = client_lender.get(URL).content.decode()

    assert "Klettergurt je Größe" in page and "Klettersteigset" in page and "Helm" in page
    assert 'aria-label="Klettergurt Größe M"' in page


def test_pickup_button_starts_the_reservation_flow(
    client_lender: Client, make_part: MakePart, borrower: Borrower, lender: User
) -> None:
    booking = reserve(
        borrower, [make_part("DAV-HL-H-1", designation="Helm")], TODAY, TODAY, actor=lender
    )

    response = client_lender.post(reverse("counter:checkout_reservations"), {"vorgang": booking.pk})

    assert response["Location"] == reverse("counter:checkout_borrower")


def test_viewer_has_no_today_page(client: Client) -> None:
    client.force_login(make_inspector(role=Role.VIEWER, qualified=False))

    assert client.get(URL).status_code == 403
