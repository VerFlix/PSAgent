"""Ausleihe verlängern (#129).

Festlegung (Felix, 01.11. und 04.11.2026): ab Verleiher, ohne Unterschrift, auch bei
Überfälligkeit; Regeln der Ausgabe für das neue Datum (nicht reserviert, nicht gesperrt, PSA-Prüfung
danach); Grenzen vom Admin mit YubiKey (ohne Eintrag 1 Verlängerung, höchstens 14 Tage); jede
Verlängerung angehängt und verkettet; Erinnerungen zählen ab dem neuen Datum; Bestätigung per Mail.
"""

from datetime import date

import pytest
from django.core import mail
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.db.backends.postgresql.psycopg_any import DateRange
from django.test import Client
from django.urls import reverse

from accounts.models import Role, User
from core import chain
from inventory.models import EventKind
from inventory.services import record_event
from lending import digest, extensions, reminders
from lending.extensions import Limits
from lending.models import Booking, Borrower, ExtensionLimit, Loan, LoanExtension, Reminder
from lending.services import LendingError, lend, reserve
from tests.helpers import make_inspector
from tests.lending.conftest import DAY, TODAY, MakePart, checkout_kwargs
from tests.yubikey_client import login_with_yubikey

pytestmark = pytest.mark.django_db
PHONE = "phone"


def lent(borrower: Borrower, make_part: MakePart, lender: User, end: date) -> Loan:
    booking = lend(borrower, [make_part()], TODAY - 3 * DAY, end, **checkout_kwargs(lender))
    return booking.loans.get()


# --- Regeln ---


def test_defaults_one_extension_fourteen_days(
    borrower: Borrower, make_part: MakePart, lender: User
) -> None:
    loan = lent(borrower, make_part, lender, TODAY + 2 * DAY)

    assert extensions.limits() == Limits(1, 14)
    assert extensions.problems(loan, TODAY + 16 * DAY) == []
    assert "höchstens 14 Tage" in extensions.problems(loan, TODAY + 17 * DAY)[0]

    extensions.extend([loan], TODAY + 9 * DAY, actor=lender, reason=PHONE)
    loan.refresh_from_db()
    assert loan.end == TODAY + 9 * DAY
    assert "schon 1-mal verlängert" in extensions.problems(loan, TODAY + 10 * DAY)[0]


def test_overdue_loan_can_be_extended_but_not_into_the_past(
    borrower: Borrower, make_part: MakePart, lender: User
) -> None:
    loan = lent(borrower, make_part, lender, TODAY - 2 * DAY)

    assert "Vergangenheit" in " ".join(extensions.problems(loan, TODAY - DAY))
    extensions.extend([loan], TODAY + 5 * DAY, actor=lender, reason=PHONE)

    loan.refresh_from_db()
    assert loan.end == TODAY + 5 * DAY and not loan.is_overdue(TODAY)


def test_reserved_part_blocks_the_extension(
    borrower: Borrower, make_part: MakePart, lender: User
) -> None:
    loan = lent(borrower, make_part, lender, TODAY + 2 * DAY)
    reserve(borrower, [loan.part], TODAY + 5 * DAY, TODAY + 6 * DAY, actor=lender)

    with pytest.raises(LendingError, match="im Zeitraum bereits vergeben"):
        extensions.extend([loan], TODAY + 7 * DAY, actor=lender, reason=PHONE)
    assert extensions.problems(loan, TODAY + 4 * DAY) == []


def test_locked_part_and_inspection_block_the_extension(
    borrower: Borrower, make_part: MakePart, lender: User
) -> None:
    locked = lent(borrower, make_part, lender, TODAY + 2 * DAY)
    record_event(locked.part, EventKind.LOCKED, actor=lender, comment="Riss (Testdaten)")
    soon = lend(
        borrower,
        [make_part(next_due=TODAY + 5 * DAY)],
        TODAY,
        TODAY + 2 * DAY,
        **checkout_kwargs(lender),
    ).loans.get()

    assert "ist gesperrt" in extensions.problems(locked, TODAY + 4 * DAY)
    assert any("PSA-Prüfung" in reason for reason in extensions.problems(soon, TODAY + 6 * DAY))
    assert extensions.problems(soon, TODAY + 5 * DAY) == []


def test_all_or_nothing(borrower: Borrower, make_part: MakePart, lender: User) -> None:
    booking = lend(
        borrower, [make_part(), make_part()], TODAY, TODAY + 2 * DAY, **checkout_kwargs(lender)
    )
    first, second = booking.loans.order_by("pk")
    reserve(borrower, [second.part], TODAY + 4 * DAY, TODAY + 4 * DAY, actor=lender)

    with pytest.raises(LendingError):
        extensions.extend([first, second], TODAY + 5 * DAY, actor=lender, reason=PHONE)

    first.refresh_from_db()
    assert first.end == TODAY + 2 * DAY and not LoanExtension.objects.exists()


def test_extension_is_appended_and_chained(
    borrower: Borrower, make_part: MakePart, lender: User
) -> None:
    loan = lent(borrower, make_part, lender, TODAY + 2 * DAY)

    (entry,) = extensions.extend(
        [loan], TODAY + 9 * DAY, actor=lender, reason="in_person", comment=" kurz angerufen "
    )

    assert (entry.old_end, entry.new_end, entry.reason, entry.comment, entry.actor) == (
        TODAY + 2 * DAY,
        TODAY + 9 * DAY,
        "in_person",
        "kurz angerufen",
        lender,
    )
    assert chain.verify() == []


def test_database_allows_a_later_end_only_with_its_extension(
    borrower: Borrower, make_part: MakePart, lender: User
) -> None:
    """Regel 3: Der Zeitraum ändert sich nur durch eine angehängte Verlängerung, auch in der
    Datenbank (Trigger lending_loan_guard, Migration 0009)."""
    loan = lent(borrower, make_part, lender, TODAY + 2 * DAY)
    later = DateRange(loan.start, TODAY + 9 * DAY, bounds="[]")

    with pytest.raises(IntegrityError, match="nur durch eine Verlängerung"), transaction.atomic():
        Loan.objects.filter(pk=loan.pk).update(period=later)
    with pytest.raises(IntegrityError, match="nur durch eine Verlängerung"), transaction.atomic():
        LoanExtension.objects.create(
            loan=loan, old_end=loan.end, new_end=TODAY + 8 * DAY, reason=PHONE
        )
        Loan.objects.filter(pk=loan.pk).update(period=later)
    with pytest.raises(IntegrityError, match="nur durch eine Verlängerung"), transaction.atomic():
        Loan.objects.filter(pk=loan.pk).update(
            period=DateRange(loan.start - DAY, loan.end, bounds="[]")
        )

    loan.refresh_from_db()
    assert loan.end == TODAY + 2 * DAY


def test_reason_is_required(borrower: Borrower, make_part: MakePart, lender: User) -> None:
    loan = lent(borrower, make_part, lender, TODAY + 2 * DAY)

    with pytest.raises(LendingError, match="Grund"):
        extensions.extend([loan], TODAY + 9 * DAY, actor=lender, reason="")


@pytest.mark.parametrize("role", [Role.VIEWER])
def test_viewer_may_not_extend(
    borrower: Borrower, make_part: MakePart, lender: User, role: Role
) -> None:
    loan = lent(borrower, make_part, lender, TODAY + 2 * DAY)

    with pytest.raises(PermissionDenied):
        extensions.extend(
            [loan], TODAY + 9 * DAY, actor=make_inspector(role=role, qualified=False), reason=PHONE
        )


def test_admin_limits_take_effect(borrower: Borrower, make_part: MakePart, lender: User) -> None:
    admin = make_inspector(role=Role.ADMIN)
    extensions.set_limits(2, 3, actor=admin)
    loan = lent(borrower, make_part, lender, TODAY + 2 * DAY)

    assert "höchstens 3 Tage" in extensions.problems(loan, TODAY + 6 * DAY)[0]
    extensions.extend([loan], TODAY + 5 * DAY, actor=lender, reason=PHONE)
    loan.refresh_from_db()
    extensions.extend([loan], TODAY + 8 * DAY, actor=lender, reason=PHONE)
    loan.refresh_from_db()
    assert "schon 2-mal" in extensions.problems(loan, TODAY + 9 * DAY)[0]


@pytest.mark.parametrize(("count", "days"), [(11, 14), (1, 0), (1, 91)])
def test_limits_have_bounds(count: int, days: int) -> None:
    with pytest.raises(LendingError):
        extensions.set_limits(count, days, actor=make_inspector(role=Role.ADMIN))


@pytest.mark.parametrize("role", [Role.PPE_EXPERT, Role.TRAINER, Role.LENDER, Role.VIEWER])
def test_only_admin_sets_limits(role: Role) -> None:
    with pytest.raises(PermissionDenied):
        extensions.set_limits(2, 7, actor=make_inspector(role=role))


# --- Erinnerungen und Sammelmail ---


def test_reminders_count_from_the_new_date(
    borrower: Borrower, make_part: MakePart, lender: User
) -> None:
    loan = lent(borrower, make_part, lender, TODAY - 2 * DAY)
    reminders.run(TODAY)
    assert list(Reminder.objects.values_list("number", flat=True)) == [1]

    extensions.extend([loan], TODAY + 3 * DAY, actor=lender, reason=PHONE)

    assert reminders.due(TODAY + 3 * DAY) == []  # neues Datum noch nicht vorbei
    sent = reminders.run(TODAY + 4 * DAY)  # erster Tag nach dem neuen Datum
    assert [entry.number for entry in sent] == [1]
    first, second = Reminder.objects.order_by("sent_at", "pk")
    assert (first.number, first.extension_id) == (1, None)
    assert second.number == 1 and second.extension_id is not None


def test_extended_loan_leaves_the_digest(
    borrower: Borrower, make_part: MakePart, lender: User
) -> None:
    loan = lent(borrower, make_part, lender, TODAY - 2 * DAY)
    assert digest.overdue(TODAY)

    extensions.extend([loan], TODAY + 3 * DAY, actor=lender, reason=PHONE)

    assert digest.overdue(TODAY) == []


# --- Bestätigung ---


def test_confirmation_mail_names_old_and_new_date(
    borrower: Borrower, make_part: MakePart, lender: User
) -> None:
    loan = lent(borrower, make_part, lender, TODAY + 2 * DAY)
    created = extensions.extend([loan], TODAY + 9 * DAY, actor=lender, reason=PHONE)

    message = extensions.confirmation(created)

    assert message is not None and message.to == ["kim.beispiel@example.org"]
    assert f"bisher zurück bis {TODAY + 2 * DAY:%d.%m.%Y}" in message.body
    assert f"jetzt zurück bis {TODAY + 9 * DAY:%d.%m.%Y}" in message.body


# --- Oberfläche ---


def extend_url(booking: Booking) -> str:
    return reverse("lending:booking_extend", args=[booking.pk])


def test_pc_extend_with_confirmation(
    client: Client, borrower: Borrower, make_part: MakePart, lender: User
) -> None:
    loan = lent(borrower, make_part, lender, TODAY + 2 * DAY)
    client.force_login(lender)

    page = client.get(extend_url(loan.booking)).content.decode()
    response = client.post(
        extend_url(loan.booking),
        {
            "ausleihe": [loan.pk],
            "new_end": (TODAY + 9 * DAY).isoformat(),
            "reason": PHONE,
            "confirm": "on",
        },
        follow=True,
    )

    assert f'value="{(TODAY + 9 * DAY).isoformat()}"' in page  # Vorschlag: 1 Woche
    content = response.content.decode()
    assert "Verlängert: 1 Teil bis" in content and "Bestätigung per E-Mail gesendet." in content
    assert "bei der Ausgabe unterschrieben" in content
    assert len(mail.outbox) == 1
    loan.refresh_from_db()
    assert loan.end == TODAY + 9 * DAY


def test_pc_extend_shows_the_reason_why_not(
    client: Client, borrower: Borrower, make_part: MakePart, lender: User
) -> None:
    loan = lent(borrower, make_part, lender, TODAY + 2 * DAY)
    client.force_login(lender)

    response = client.post(
        extend_url(loan.booking),
        {"ausleihe": [loan.pk], "new_end": (TODAY + 30 * DAY).isoformat(), "reason": PHONE},
    )

    assert response.status_code == 400 and "höchstens 14 Tage" in response.content.decode()


def test_counter_extend_from_the_return_page(
    client: Client, borrower: Borrower, make_part: MakePart, lender: User
) -> None:
    loan = lent(borrower, make_part, lender, TODAY + 2 * DAY)
    client.force_login(lender)

    page = client.get(reverse("counter:return_booking", args=[loan.booking_id])).content.decode()
    response = client.post(
        reverse("counter:extend", args=[loan.booking_id]),
        {"ausleihe": [loan.pk], "new_end": (TODAY + 5 * DAY).isoformat(), "reason": "email"},
    )

    assert reverse("counter:extend", args=[loan.booking_id]) in page
    assert response.status_code == 302 and response["Location"] == reverse("counter:home")
    loan.refresh_from_db()
    assert loan.end == TODAY + 5 * DAY


def test_admin_sets_limits_with_yubikey(client: Client) -> None:
    admin = make_inspector(role=Role.ADMIN)
    login_with_yubikey(client, admin)
    url = reverse("lending:extension_limits")

    assert client.get(url).status_code == 200
    response = client.post(url, {"max_count": 2, "max_days": 7})

    assert response.status_code == 302
    setting = ExtensionLimit.objects.get()
    assert (setting.max_count, setting.max_days, setting.actor) == (2, 7, admin)


def test_limits_without_yubikey_are_refused(client: Client) -> None:
    client.force_login(make_inspector(role=Role.ADMIN))

    response = client.post(reverse("lending:extension_limits"), {"max_count": 2, "max_days": 7})

    assert response.status_code == 403 and not ExtensionLimit.objects.exists()
