"""Erinnerung bei Überfälligkeit per E-Mail (#72, Parität 18 „zusätzlich optionale Erinnerung“).

Takt: am ersten Tag nach der geplanten Rückgabe, dann nach 7 und nach 14 Tagen, höchstens 3
Mails je Ausleihe und je Lauf höchstens eine. Abschaltbar für alle (nur Admin) und je Vorgang
(ab Verleiher). Gespeichert wird nur, welche Erinnerung wann rausging, kein Inhalt.
"""

from datetime import date
from io import StringIO
from typing import Any

import pytest
from django.core import mail
from django.core.exceptions import PermissionDenied
from django.core.management import call_command
from django.test import Client, override_settings
from django.urls import reverse

from accounts.models import Role, User
from core.append_only import AppendOnlyError
from lending import reminders
from lending.models import Booking, Borrower, Reminder
from lending.services import create_borrower, lend, return_loans
from privacy.notice import retention_notice
from tests.helpers import make_inspector
from tests.lending.conftest import DAY, TODAY, MakePart, checkout_kwargs, return_kwargs

pytestmark = pytest.mark.django_db
END = TODAY + DAY
FAILING = {"default": {"BACKEND": "tests.lending.test_receipts.FailingBackend"}}


def lent(make_part: MakePart, borrower: Borrower, lender: User, count: int = 1) -> Booking:
    parts = [make_part() for _ in range(count)]
    return lend(borrower, parts, TODAY, END, **checkout_kwargs(lender))


def run(on: date) -> list[Any]:
    return reminders.run(on)


# --- Takt ---


def test_first_reminder_the_day_after(
    make_part: MakePart, borrower: Borrower, lender: User
) -> None:
    booking = lent(make_part, borrower, lender)

    assert run(END) == [] and mail.outbox == []
    run(END + DAY)

    [message] = mail.outbox
    assert message.to == ["kim.beispiel@example.org"]
    assert message.subject == f"Erinnerung: Rückgabe überfällig, Ausleihvorgang #{booking.pk}"
    part = booking.loans.get().part
    assert f"{part.name} (EI {part.components.get().ei}), zurück bis {END:%d.%m.%Y}" in message.body
    assert "1. Erinnerung (von höchstens 3)" in message.body
    assert retention_notice() in message.body


def test_rhythm_1_7_14_and_at_most_three(
    make_part: MakePart, borrower: Borrower, lender: User
) -> None:
    lent(make_part, borrower, lender)

    sent_on = [offset for offset in range(0, 40) if run(END + offset * DAY)]

    assert sent_on == [1, 8, 15]
    assert len(mail.outbox) == 3
    assert list(Reminder.objects.values_list("number", flat=True).order_by("number")) == [1, 2, 3]


def test_one_mail_per_run_after_a_gap(
    make_part: MakePart, borrower: Borrower, lender: User
) -> None:
    """War der Server tagelang aus, kommt nicht alles auf einmal."""
    lent(make_part, borrower, lender)

    run(END + 20 * DAY)

    assert len(mail.outbox) == 1
    assert Reminder.objects.get().number == 1


def test_one_mail_per_booking(make_part: MakePart, borrower: Borrower, lender: User) -> None:
    booking = lent(make_part, borrower, lender, count=2)

    run(END + DAY)

    [message] = mail.outbox
    for loan in booking.loans.all():
        assert loan.part.name in message.body
    assert Reminder.objects.count() == 2


def test_returned_loans_get_no_reminder(
    make_part: MakePart, borrower: Borrower, lender: User
) -> None:
    booking = lent(make_part, borrower, lender)
    return_loans(list(booking.loans.all()), **return_kwargs(lender))

    assert run(END + DAY) == [] and mail.outbox == []


def test_no_email_no_reminder(make_part: MakePart, lender: User) -> None:
    lent(make_part, create_borrower("Noa Probe", address="x", phone="0000 1"), lender)

    assert run(END + DAY) == []
    assert mail.outbox == [] and not Reminder.objects.exists()


@override_settings(MAILERS=FAILING)
def test_failed_mail_is_retried_next_run(
    make_part: MakePart, borrower: Borrower, lender: User
) -> None:
    lent(make_part, borrower, lender)

    assert run(END + DAY) == []
    assert not Reminder.objects.exists()


def test_reminder_log_is_append_only(make_part: MakePart, borrower: Borrower, lender: User) -> None:
    lent(make_part, borrower, lender)
    run(END + DAY)

    with pytest.raises(AppendOnlyError):
        Reminder.objects.all().delete()


# --- Abschalten ---


def test_switched_off_for_everyone(make_part: MakePart, borrower: Borrower, lender: User) -> None:
    lent(make_part, borrower, lender)
    admin = make_inspector(role=Role.ADMIN, qualified=False)

    reminders.set_enabled(False, actor=admin)
    assert run(END + DAY) == [] and not reminders.enabled()

    reminders.set_enabled(True, actor=admin)
    assert len(run(END + DAY)) == 1


@pytest.mark.parametrize("role", [Role.PPE_EXPERT, Role.TRAINER, Role.LENDER, Role.VIEWER])
def test_only_admin_switches_for_everyone(role: Role, db: None) -> None:
    """Verbotener Fall: Für alle schaltet nur Admin; nichts ändert sich."""
    with pytest.raises(PermissionDenied, match="ab der Rolle Admin"):
        reminders.set_enabled(False, actor=make_inspector(role=role, qualified=False))

    assert reminders.enabled()


def test_switched_off_for_one_booking(
    make_part: MakePart, borrower: Borrower, lender: User
) -> None:
    booking = lent(make_part, borrower, lender)
    other = lent(
        make_part, create_borrower("Kai Anders", address="x", email="kai@example.org"), lender
    )

    reminders.set_booking_enabled(booking, False, actor=lender)
    run(END + DAY)

    assert [message.to for message in mail.outbox] == [["kai@example.org"]]
    assert not reminders.booking_enabled(booking) and reminders.booking_enabled(other)


def test_viewer_cannot_switch_booking(
    make_part: MakePart, borrower: Borrower, lender: User
) -> None:
    booking = lent(make_part, borrower, lender)

    with pytest.raises(PermissionDenied):
        reminders.set_booking_enabled(
            booking, False, actor=make_inspector(role=Role.VIEWER, qualified=False)
        )

    assert reminders.booking_enabled(booking)


# --- Befehl ---


def test_command_dry_run_and_numbers_only(
    make_part: MakePart, borrower: Borrower, lender: User
) -> None:
    booking = lent(make_part, borrower, lender)
    out = StringIO()

    call_command("erinnerungen", "--probelauf", f"--stichtag={END + DAY:%Y-%m-%d}", stdout=out)
    assert mail.outbox == [] and not Reminder.objects.exists()
    assert f"Ausleihvorgang #{booking.pk}: 1. Erinnerung" in out.getvalue()

    call_command("erinnerungen", f"--stichtag={END + DAY:%Y-%m-%d}", stdout=out)
    assert len(mail.outbox) == 1
    text = out.getvalue()
    assert "1 Erinnerung(en) gesendet" in text
    assert "Kim" not in text and "@" not in text


# --- Oberfläche ---


def test_booking_detail_shows_reminders_and_switch(
    client: Client, make_part: MakePart, borrower: Borrower, lender: User
) -> None:
    booking = lent(make_part, borrower, lender)
    run(END + DAY)
    client.force_login(lender)
    url = reverse("lending:booking_detail", args=[booking.pk])

    page = client.get(url).content.decode()
    client.post(reverse("lending:booking_reminders", args=[booking.pk]), {"an": "0"})

    assert "1. Erinnerung gesendet am" in page
    assert "Keine Erinnerungen für diesen Vorgang" in page
    assert not reminders.booking_enabled(booking)
    assert "Erinnerungen für diesen Vorgang wieder einschalten" in client.get(url).content.decode()


def test_admin_switches_on_overview(client: Client) -> None:
    client.force_login(make_inspector(role=Role.ADMIN, qualified=False))

    page = client.get(reverse("lending:overview")).content.decode()
    client.post(reverse("lending:reminder_switch"), {"an": "0"})

    assert "E-Mail-Erinnerungen bei Überfälligkeit: an" in page
    assert not reminders.enabled()
    assert (
        "E-Mail-Erinnerungen bei Überfälligkeit: aus"
        in client.get(reverse("lending:overview")).content.decode()
    )


@pytest.mark.parametrize("role", [Role.PPE_EXPERT, Role.TRAINER, Role.LENDER])
def test_switch_view_forbidden_below_admin(role: Role, client: Client) -> None:
    client.force_login(make_inspector(role=role, qualified=False))

    page = client.get(reverse("lending:overview")).content.decode()
    response = client.post(reverse("lending:reminder_switch"), {"an": "0"})

    assert "Erinnerungen für alle ausschalten" not in page
    assert response.status_code == 403 and reminders.enabled()
