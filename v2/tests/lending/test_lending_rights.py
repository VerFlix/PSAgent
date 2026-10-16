"""Rechte im Verleih, geprüft im Dienst (#12): verbotene Fälle ändern nichts."""

from collections.abc import Callable
from typing import Any

import pytest
from django.core.exceptions import PermissionDenied

from accounts.models import Role, User
from inventory.models import PartEvent
from lending.models import Booking, Borrower, BorrowerContact, Loan, LoanEvent, LoanStatus
from lending.services import (
    cancel,
    check_out,
    create_borrower,
    erase_contact,
    lend,
    reserve,
    return_loans,
    update_contact,
)
from tests.helpers import make_inspector
from tests.lending.conftest import DAY, TODAY, MakePart, checkout_kwargs, return_kwargs


def snapshot() -> tuple[Any, ...]:
    return (
        Borrower.objects.count(),
        Booking.objects.count(),
        list(Loan.objects.order_by("pk").values_list("pk", "status")),
        LoanEvent.objects.count(),
        PartEvent.objects.count(),
        list(BorrowerContact.objects.order_by("pk").values_list("name", "address", "phone")),
    )


def user(role: Role) -> User:
    return make_inspector(role=role, qualified=False)


@pytest.fixture
def viewer(db: None) -> User:
    return user(Role.VIEWER)


Action = Callable[[User], object]


@pytest.fixture
def lending_actions(make_part: MakePart, borrower: Borrower, lender: User) -> dict[str, Action]:
    """Jeder Verleihvorgang einmal, mit vorbereiteten Reservierungen und Ausleihen."""
    reserved = list(reserve(borrower, [make_part()], TODAY, TODAY + DAY, actor=lender).loans.all())
    lent = list(
        lend(borrower, [make_part()], TODAY, TODAY + DAY, **checkout_kwargs(lender)).loans.all()
    )
    part = make_part()
    return {
        "Entleiher anlegen": lambda actor: create_borrower("Noa Probe", actor=actor),
        "reservieren": lambda actor: reserve(borrower, [part], TODAY, TODAY, actor=actor),
        "ausgeben": lambda actor: lend(borrower, [part], TODAY, TODAY, **checkout_kwargs(actor)),
        "Reservierung ausgeben": lambda actor: check_out(reserved, **checkout_kwargs(actor)),
        "zurücknehmen": lambda actor: return_loans(lent, **return_kwargs(actor)),
        "stornieren": lambda actor: cancel(reserved, actor=actor),
    }


@pytest.mark.parametrize(
    "action",
    [
        "Entleiher anlegen",
        "reservieren",
        "ausgeben",
        "Reservierung ausgeben",
        "zurücknehmen",
        "stornieren",
    ],
)
def test_viewer_may_not_lend(action: str, lending_actions: dict[str, Action], viewer: User) -> None:
    """Verbotener Fall: Viewer reservieren, geben aus und nehmen zurück nicht."""
    before = snapshot()

    with pytest.raises(PermissionDenied, match="ab der Rolle Verleiher"):
        lending_actions[action](viewer)

    assert snapshot() == before


@pytest.mark.parametrize(
    "action",
    [
        "Entleiher anlegen",
        "reservieren",
        "ausgeben",
        "Reservierung ausgeben",
        "zurücknehmen",
        "stornieren",
    ],
)
def test_lender_may_lend(action: str, lending_actions: dict[str, Action], lender: User) -> None:
    lending_actions[action](lender)


def test_lender_cancels_only_own_reservations(
    make_part: MakePart, borrower: Borrower, lender: User
) -> None:
    """Reservierungen anderer: Verleiher nur eigene, verbotener Fall ändert nichts."""
    other = user(Role.LENDER)
    foreign = list(reserve(borrower, [make_part()], TODAY, TODAY, actor=other).loans.all())
    own = list(reserve(borrower, [make_part()], TODAY, TODAY, actor=lender).loans.all())
    before = snapshot()

    with pytest.raises(PermissionDenied, match="nur eigene"):
        cancel(foreign, actor=lender)
    assert snapshot() == before

    cancel(own, actor=lender)
    assert Loan.objects.get(pk=own[0].pk).status == LoanStatus.CANCELLED


def test_mixed_cancellation_is_rejected_as_a_whole(
    make_part: MakePart, borrower: Borrower, lender: User
) -> None:
    other = user(Role.LENDER)
    foreign = list(reserve(borrower, [make_part()], TODAY, TODAY, actor=other).loans.all())
    own = list(reserve(borrower, [make_part()], TODAY, TODAY, actor=lender).loans.all())
    before = snapshot()

    with pytest.raises(PermissionDenied):
        cancel(own + foreign, actor=lender)

    assert snapshot() == before


@pytest.mark.parametrize("role", [Role.TRAINER, Role.PPE_EXPERT, Role.ADMIN])
def test_trainer_and_above_cancel_reservations_of_others(
    role: Role, make_part: MakePart, borrower: Borrower, lender: User
) -> None:
    foreign = list(reserve(borrower, [make_part()], TODAY, TODAY, actor=lender).loans.all())

    cancel(foreign, actor=user(role))

    assert Loan.objects.get(pk=foreign[0].pk).status == LoanStatus.CANCELLED


def test_imported_reservation_is_not_own_for_lender(
    make_part: MakePart, borrower: Borrower, lender: User
) -> None:
    """Übernommene Reservierungen haben kein „angelegt von“; stornieren ab Trainer."""
    imported = list(reserve(borrower, [make_part()], TODAY, TODAY, actor=None).loans.all())

    with pytest.raises(PermissionDenied):
        cancel(imported, actor=lender)
    cancel(imported, actor=user(Role.TRAINER))


def test_lender_updates_contacts_only_during_loan(
    make_part: MakePart, borrower: Borrower, lender: User
) -> None:
    """Kontaktdaten ändern nur, wer sie sehen darf: Verleiher nur bei laufender Ausleihe."""
    before = snapshot()

    with pytest.raises(PermissionDenied, match="nur laufende Ausleihen"):
        update_contact(borrower, actor=lender, phone="0000 111111")
    assert snapshot() == before

    reserve(borrower, [make_part()], TODAY, TODAY + DAY, actor=lender)
    update_contact(borrower, actor=lender, phone="0000 111111")
    assert BorrowerContact.objects.get(borrower=borrower).phone == "0000 111111"


def test_expert_updates_contacts_without_loan(borrower: Borrower) -> None:
    update_contact(borrower, actor=user(Role.PPE_EXPERT), phone="0000 222222")

    assert BorrowerContact.objects.get(borrower=borrower).phone == "0000 222222"


def test_viewer_may_not_update_contacts(borrower: Borrower, viewer: User) -> None:
    before = snapshot()

    with pytest.raises(PermissionDenied):
        update_contact(borrower, actor=viewer, phone="0000 333333")

    assert snapshot() == before


@pytest.mark.parametrize("role", [Role.PPE_EXPERT, Role.TRAINER, Role.LENDER, Role.VIEWER])
def test_only_admin_erases_contacts(role: Role, borrower: Borrower) -> None:
    before = snapshot()

    with pytest.raises(PermissionDenied, match="ab der Rolle Admin"):
        erase_contact(borrower, actor=user(role))

    assert snapshot() == before


def test_admin_erases_contacts(borrower: Borrower) -> None:
    erase_contact(borrower, actor=user(Role.ADMIN))

    assert not BorrowerContact.objects.filter(borrower=borrower).exists()
