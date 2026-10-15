"""Entleiher: wiederverwendbar, Kontaktdaten nachtragen und nach der Löschfrist löschen.

Kontaktdaten stehen getrennt von den Ausleihen (Konzept, Abschnitte 4 und 7). Nach dem Löschen
bleibt die Ausleihhistorie vollständig erhalten, nur ohne Personendaten.
"""

import pytest
from django.db import IntegrityError, transaction
from lending.models import Borrower, BorrowerContact, Loan
from lending.services import create_borrower, erase_contact, lend, update_contact

from accounts.models import User
from tests.lending.conftest import DAY, TODAY, MakePart, checkout_kwargs


def test_borrower_is_reused_across_bookings(
    make_part: MakePart, borrower: Borrower, lender: User
) -> None:
    """Parität 14: Entleiher wiederverwendbar."""
    lend(borrower, [make_part()], TODAY, TODAY + DAY, **checkout_kwargs(lender))
    lend(borrower, [make_part()], TODAY, TODAY + DAY, **checkout_kwargs(lender))

    assert Borrower.objects.count() == 1
    assert borrower.bookings.count() == 2


def test_contact_data_can_be_added_later(db: None) -> None:
    """Parität 16: Kontaktdaten nachtragen."""
    person = create_borrower("Robin Exempel")

    update_contact(person, address="Beispielweg 4", phone="0000 654321")

    person.refresh_from_db()
    assert (person.contact.address, person.contact.phone) == ("Beispielweg 4", "0000 654321")


def test_erasing_contact_keeps_history(
    make_part: MakePart, borrower: Borrower, lender: User
) -> None:
    booking = lend(borrower, [make_part()], TODAY, TODAY + DAY, **checkout_kwargs(lender))

    erase_contact(borrower)

    borrower = Borrower.objects.get(pk=borrower.pk)
    assert not BorrowerContact.objects.filter(borrower=borrower).exists()
    assert Loan.objects.filter(booking=booking).count() == 1
    assert borrower.display_name == f"Entleiher #{borrower.pk} (Kontaktdaten gelöscht)"


def test_display_name_uses_contact(borrower: Borrower) -> None:
    assert borrower.display_name == "Kim Beispiel"


@pytest.mark.parametrize("name", ["", "   "])
def test_contact_needs_a_name(name: str, db: None) -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        create_borrower(name)


def test_borrower_with_bookings_cannot_be_deleted(
    make_part: MakePart, borrower: Borrower, lender: User
) -> None:
    lend(borrower, [make_part()], TODAY, TODAY + DAY, **checkout_kwargs(lender))

    with pytest.raises(IntegrityError):
        borrower.delete()
