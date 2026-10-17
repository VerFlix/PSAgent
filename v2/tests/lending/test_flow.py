"""Reservieren, ausgeben, zurücknehmen, stornieren (Parität 12, 14, 15, 17, 18)."""

import pytest
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, connection, transaction

from accounts.models import Role, User
from inventory.models import EventKind, PartStatus
from inventory.services import record_event
from lending.models import (
    Borrower,
    Loan,
    LoanEvent,
    LoanEventKind,
    LoanStatus,
    Signature,
    SignatureOrigin,
)
from lending.services import (
    LendingError,
    cancel,
    check_out,
    create_borrower,
    lend,
    reserve,
    return_loans,
)
from tests.lending.conftest import (
    DAY,
    TODAY,
    MakePart,
    checkout_kwargs,
    return_kwargs,
)


def test_reservation_is_converted_into_loan(
    make_part: MakePart, borrower: Borrower, lender: User
) -> None:
    """Parität 15: Reservierung in Ausleihe umwandeln, mit denselben Prüfungen."""
    booking = reserve(borrower, [make_part(), make_part()], TODAY, TODAY + 3 * DAY, actor=lender)

    check_out(list(booking.loans.all()), **checkout_kwargs(lender))

    loans = list(booking.loans.all())
    assert {loan.status for loan in loans} == {LoanStatus.LENT}
    events = LoanEvent.objects.filter(loan__in=loans, kind=LoanEventKind.CHECKED_OUT)
    assert events.count() == 2
    assert {event.actor for event in events} == {lender}
    assert len({event.signature_id for event in events}) == 1  # eine Unterschrift für alles


def test_direct_loan_without_reservation(
    make_part: MakePart, borrower: Borrower, lender: User
) -> None:
    """Parität 12: direkt ausleihen, mehrere Teile, Zeitraum."""
    booking = lend(
        borrower, [make_part(), make_part()], TODAY, TODAY + 2 * DAY, **checkout_kwargs(lender)
    )

    assert {loan.status for loan in booking.loans.all()} == {LoanStatus.LENT}


def test_return_with_optional_lock(make_part: MakePart, borrower: Borrower, lender: User) -> None:
    """Parität 17: Rückgabe mit Kommentar, Kurzprüfung, Unterschrift, optional sperren."""
    first, second = make_part(), make_part()
    booking = lend(borrower, [first, second], TODAY, TODAY + 2 * DAY, **checkout_kwargs(lender))
    loans = {loan.part_id: loan for loan in booking.loans.all()}

    return_loans([loans[first.pk]], comment="alles gut", **return_kwargs(lender))
    return_loans(
        [loans[second.pk]], comment="Naht offen", lock_comment="Naht offen", **return_kwargs(lender)
    )

    assert first.status() == PartStatus.RELEASED
    assert second.status() == PartStatus.LOCKED
    returned = LoanEvent.objects.get(loan=loans[second.pk], kind=LoanEventKind.RETURNED)
    assert (returned.actor, returned.comment) == (lender, "Naht offen")


def test_return_frees_part_for_new_booking(
    make_part: MakePart, borrower: Borrower, lender: User
) -> None:
    part = make_part()
    booking = lend(borrower, [part], TODAY, TODAY + 5 * DAY, **checkout_kwargs(lender))
    return_loans(list(booking.loans.all()), **return_kwargs(lender))

    reserve(borrower, [part], TODAY + DAY, TODAY + 2 * DAY, actor=lender)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"quick_check": False}, "Kurzkontrolle"),
        ({"gal_provided": False}, "GAL"),
        ({"signature": []}, "Unterschrift"),
        ({"signature": "kaputt"}, "Unterschrift"),
        ({"signer_name": " "}, "Name"),
    ],
)
def test_checkout_needs_all_confirmations(
    change: dict[str, object], message: str, make_part: MakePart, borrower: Borrower, lender: User
) -> None:
    with pytest.raises(LendingError, match=message):
        lend(borrower, [make_part()], TODAY, TODAY + DAY, **{**checkout_kwargs(lender), **change})

    assert not Loan.objects.exists()


@pytest.mark.parametrize(
    ("contact", "message"),
    [
        ({"address": ""}, "Adresse"),
        ({"email": "", "phone": ""}, "E-Mail oder Telefon"),
    ],
)
def test_checkout_needs_contact_data(
    contact: dict[str, str], message: str, make_part: MakePart, lender: User
) -> None:
    fields = {"address": "Probegasse 2", "email": "noa@example.org", **contact}
    person = create_borrower(
        "Noa Probe", address=fields["address"], email=fields["email"], phone=fields.get("phone", "")
    )
    booking = reserve(person, [make_part()], TODAY, TODAY + DAY, actor=lender)

    with pytest.raises(LendingError, match=message):
        check_out(list(booking.loans.all()), **checkout_kwargs(lender))


def test_phone_instead_of_email_is_enough(make_part: MakePart, lender: User) -> None:
    person = create_borrower("Noa Probe", address="Probegasse 2", phone="0000 123456")

    lend(person, [make_part()], TODAY, TODAY + DAY, **checkout_kwargs(lender))


def test_checkout_needs_gal_document(make_part: MakePart, borrower: Borrower, lender: User) -> None:
    with pytest.raises(LendingError, match="keine GAL"):
        lend(borrower, [make_part(gal=False)], TODAY, TODAY + DAY, **checkout_kwargs(lender))


def test_self_issued_loan_is_marked(make_part: MakePart, lender: User) -> None:
    myself = create_borrower(
        "Testperson Muster", address="Musterallee 3", email="muster@example.org", user=lender
    )
    other = create_borrower("Kim Beispiel", address="Teststraße 1", email="kim@example.org")

    own = lend(myself, [make_part()], TODAY, TODAY + DAY, **checkout_kwargs(lender))
    foreign = lend(other, [make_part()], TODAY, TODAY + DAY, **checkout_kwargs(lender))

    assert LoanEvent.objects.get(loan__booking=own).self_issued is True
    assert LoanEvent.objects.get(loan__booking=foreign).self_issued is False


def test_return_needs_signature(make_part: MakePart, borrower: Borrower, lender: User) -> None:
    booking = lend(borrower, [make_part()], TODAY, TODAY + DAY, **checkout_kwargs(lender))

    with pytest.raises(LendingError, match="Unterschrift"):
        return_loans(list(booking.loans.all()), **{**return_kwargs(lender), "signature": []})


@pytest.mark.parametrize(
    "action",
    ["checkout_lent", "return_reserved", "cancel_lent", "return_twice"],
)
def test_invalid_transitions_are_rejected(
    action: str, make_part: MakePart, borrower: Borrower, lender: User
) -> None:
    reserved = list(reserve(borrower, [make_part()], TODAY, TODAY + DAY, actor=lender).loans.all())
    lent = list(
        lend(borrower, [make_part()], TODAY, TODAY + DAY, **checkout_kwargs(lender)).loans.all()
    )

    with pytest.raises(LendingError):
        if action == "checkout_lent":
            check_out(lent, **checkout_kwargs(lender))
        elif action == "return_reserved":
            return_loans(reserved, **return_kwargs(lender))
        elif action == "cancel_lent":
            cancel(lent, actor=lender, comment="zu spät")
        else:
            return_loans(lent, **return_kwargs(lender))
            return_loans(lent, **return_kwargs(lender))


def test_overdue_is_derived(make_part: MakePart, borrower: Borrower, lender: User) -> None:
    """Parität 18: Hinweis „überfällig“."""
    late = lend(borrower, [make_part()], TODAY - 5 * DAY, TODAY - DAY, **checkout_kwargs(lender))
    on_time = lend(borrower, [make_part()], TODAY - DAY, TODAY, **checkout_kwargs(lender))

    assert late.loans.get().is_overdue(TODAY) is True
    assert on_time.loans.get().is_overdue(TODAY) is False


def test_overdue_part_is_not_available_again(
    make_part: MakePart, borrower: Borrower, lender: User
) -> None:
    part = make_part()
    lend(borrower, [part], TODAY - 5 * DAY, TODAY - DAY, **checkout_kwargs(lender))

    with pytest.raises(LendingError, match="noch nicht zurückgegeben"):
        reserve(borrower, [part], TODAY + DAY, TODAY + 2 * DAY, actor=lender)


@pytest.mark.parametrize("state", ["new", "locked", "retired"])
def test_unavailable_parts_cannot_be_reserved_or_lent(
    state: str, make_part: MakePart, borrower: Borrower, lender: User, inspector: User
) -> None:
    """Parität 13: nur nicht gesperrte, freigegebene, nicht ausgesonderte Teile."""
    part = make_part(released=state != "new")
    if state == "locked":
        record_event(part, EventKind.LOCKED, actor=lender, comment="Sichtprüfung auffällig")
    if state == "retired":
        record_event(part, EventKind.RETIRED, actor=inspector, comment="Ablegereife")

    with pytest.raises(LendingError):
        reserve(borrower, [part], TODAY, TODAY + DAY, actor=lender)
    with pytest.raises(LendingError):
        lend(borrower, [part], TODAY, TODAY + DAY, **checkout_kwargs(lender))


def test_part_locked_after_reservation_cannot_be_checked_out(
    make_part: MakePart, borrower: Borrower, lender: User
) -> None:
    part = make_part()
    booking = reserve(borrower, [part], TODAY, TODAY + DAY, actor=lender)
    record_event(part, EventKind.LOCKED, actor=lender, comment="Rückruf")

    with pytest.raises(LendingError, match="gesperrt"):
        check_out(list(booking.loans.all()), **checkout_kwargs(lender))


# --- Datenbank schützt Status und Inhalt ---


@pytest.mark.parametrize(
    ("sql", "message"),
    [
        ("UPDATE lending_loan SET status = 'reserved'", "nicht erlaubt"),
        ("UPDATE lending_loan SET status = 'cancelled'", "nicht erlaubt"),
        (
            "UPDATE lending_loan SET period = daterange('2030-01-01', '2030-01-02')",
            "nur der Status",
        ),
        ("UPDATE lending_loan SET part_id = part_id", "nur der Status"),
        ("DELETE FROM lending_loan", "nicht gelöscht"),
    ],
)
def test_database_guards_loans(
    sql: str, message: str, make_part: MakePart, borrower: Borrower, lender: User
) -> None:
    lend(borrower, [make_part()], TODAY, TODAY + DAY, **checkout_kwargs(lender))
    if "part_id = part_id" in sql:
        sql = "UPDATE lending_loan SET part_id = part_id + 1000"

    with (
        pytest.raises(IntegrityError, match=message),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(sql)


@pytest.mark.parametrize("table", ["lending_loanevent", "lending_signature", "lending_booking"])
def test_events_signatures_and_bookings_are_append_only(
    table: str, make_part: MakePart, borrower: Borrower, lender: User
) -> None:
    lend(borrower, [make_part()], TODAY, TODAY + DAY, **checkout_kwargs(lender))

    with (
        pytest.raises(IntegrityError, match="nur zum Anhängen"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(f"DELETE FROM {table}")  # noqa: S608


def test_legacy_signature_is_marked(db: None) -> None:
    signature = Signature.record([[[1, 2]]], "Altbestand", SignatureOrigin.LEGACY)

    assert signature.is_legacy is True
    assert signature.get_origin_display() == "Altbestand, ungesichert"


def test_viewer_may_not_lend(make_part: MakePart, borrower: Borrower) -> None:
    """Verbotener Fall (#12): Viewer verleihen nicht; es entsteht nichts."""
    viewer = User.objects.create_user(username="v", role=Role.VIEWER)

    with pytest.raises(PermissionDenied, match="ab der Rolle Verleiher"):
        lend(borrower, [make_part()], TODAY, TODAY + DAY, **checkout_kwargs(viewer))

    assert not Loan.objects.exists()
