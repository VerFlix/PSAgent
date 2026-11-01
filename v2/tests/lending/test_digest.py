"""Tägliche Sammelmail über überfällige Ausleihen an Vereinsadressen (#128).

Festlegung (Felix, 01.11.2026): einmal täglich, nur wenn etwas überfällig ist; neu Überfällige
markiert; mit Namen des Entleihers, ohne Telefon, E-Mail und Adresse. Der Admin schaltet sie,
pflegt die Empfänger (Änderungen mit YubiKey) und kann eine Testmail senden. Der Schalter
„Erinnerungen für diesen Vorgang aus“ unterdrückt die interne Meldung nicht.
"""

from io import StringIO

import pytest
from django.core import mail
from django.core.exceptions import PermissionDenied
from django.core.management import CommandError, call_command
from django.test import Client, override_settings
from django.urls import reverse

from accounts.models import Role, User
from core.append_only import AppendOnlyError
from lending import digest, reminders
from lending.models import Booking, Borrower, DigestRun, DigestSetting
from lending.services import lend, return_loans
from tests.helpers import make_inspector
from tests.lending.conftest import DAY, TODAY, MakePart, checkout_kwargs, return_kwargs
from tests.yubikey_client import login_with_yubikey

pytestmark = pytest.mark.django_db
END = TODAY + DAY
FAILING = {"default": {"BACKEND": "tests.lending.test_receipts.FailingBackend"}}
TEAM = ["material@example.org", "vorstand@example.org"]


def lent(make_part: MakePart, borrower: Borrower, lender: User, count: int = 1) -> Booking:
    parts = [make_part() for _ in range(count)]
    return lend(borrower, parts, TODAY, END, **checkout_kwargs(lender))


@pytest.fixture
def switched_on(db: None) -> DigestSetting:
    return digest.configure(True, TEAM, actor=None)


# --- Inhalt ---


@override_settings(PSAGENT_BASE_URL="https://psagent.example.ts.net")
def test_digest_names_borrower_parts_and_days_but_no_contact_data(
    switched_on: DigestSetting, make_part: MakePart, borrower: Borrower, lender: User
) -> None:
    booking = lent(make_part, borrower, lender)
    part = booking.loans.get().part

    result = digest.run(END + 3 * DAY)

    assert result.sent and result.bookings == 1 and result.recipients == 2
    first, second = mail.outbox
    assert first.to == [TEAM[0]] and second.to == [TEAM[1]]
    assert first.subject == f"PSAgent: 1 überfälliger Ausleihvorgang ({END + 3 * DAY:%d.%m.%Y})"
    body = first.body
    assert f"Vorgang #{booking.pk} · Kim Beispiel · 3 Tage überfällig" in body
    assert f"{part.name} (EI {part.components.get().ei}), zurück bis {END:%d.%m.%Y}" in body
    assert f"https://psagent.example.ts.net/verleih/{booking.pk}/" in body
    assert "kim.beispiel@example.org" not in body
    assert "Teststraße" not in body
    assert "nicht weiterleiten" in body


def test_newly_overdue_bookings_are_marked(
    switched_on: DigestSetting, make_part: MakePart, borrower: Borrower, lender: User
) -> None:
    lent(make_part, borrower, lender)

    digest.run(END + DAY)
    digest.run(END + 2 * DAY)

    assert "[NEU] Vorgang" in mail.outbox[0].body
    assert "[NEU]" not in mail.outbox[2].body.split("\n\n", 1)[1].split("[NEU] =")[0]


def test_only_open_parts_are_listed(
    switched_on: DigestSetting, make_part: MakePart, borrower: Borrower, lender: User
) -> None:
    booking = lent(make_part, borrower, lender, count=2)
    back, still_out = list(booking.loans.order_by("pk"))
    return_loans([back], **return_kwargs(lender))

    digest.run(END + DAY)

    body = mail.outbox[0].body
    assert still_out.part.components.get().ei in body
    assert back.part.components.get().ei not in body


def test_booking_reminder_switch_does_not_silence_the_digest(
    switched_on: DigestSetting, make_part: MakePart, borrower: Borrower, lender: User
) -> None:
    booking = lent(make_part, borrower, lender)
    reminders.set_booking_enabled(booking, False, actor=lender)

    assert digest.run(END + DAY).sent
    assert len(mail.outbox) == 2


# --- Wann gesendet wird ---


def test_nothing_overdue_sends_nothing(
    switched_on: DigestSetting, make_part: MakePart, borrower: Borrower, lender: User
) -> None:
    lent(make_part, borrower, lender)

    result = digest.run(END)

    assert not result.sent and mail.outbox == [] and not DigestRun.objects.exists()


def test_switched_off_or_never_set_sends_nothing(
    make_part: MakePart, borrower: Borrower, lender: User
) -> None:
    lent(make_part, borrower, lender)
    assert not digest.run(END + DAY).sent

    digest.configure(True, TEAM, actor=None)
    digest.configure(False, TEAM, actor=None)

    assert not digest.run(END + DAY).sent
    assert mail.outbox == []


def test_at_most_one_digest_per_day(
    switched_on: DigestSetting, make_part: MakePart, borrower: Borrower, lender: User
) -> None:
    lent(make_part, borrower, lender)

    assert digest.run(END + DAY).sent
    assert not digest.run(END + DAY).sent
    assert digest.run(END + 2 * DAY).sent

    assert len(mail.outbox) == 4
    run = DigestRun.objects.get(day=END + DAY)
    assert (run.bookings, run.recipients) == (1, 2)


def test_dry_run_neither_sends_nor_records(
    switched_on: DigestSetting, make_part: MakePart, borrower: Borrower, lender: User
) -> None:
    lent(make_part, borrower, lender)

    result = digest.run(END + DAY, dry_run=True)

    assert result.bookings == 1 and not result.sent
    assert mail.outbox == [] and not DigestRun.objects.exists()


@override_settings(MAILERS=FAILING)
def test_failed_send_is_not_recorded_and_raises(
    switched_on: DigestSetting, make_part: MakePart, borrower: Borrower, lender: User
) -> None:
    lent(make_part, borrower, lender)

    with pytest.raises(digest.DigestError):
        digest.run(END + DAY)

    assert not DigestRun.objects.exists()


# --- Einstellungen ---


def test_settings_are_appended_and_the_latest_counts() -> None:
    digest.configure(True, ["a@example.org"], actor=None)
    digest.configure(True, [" B@example.org ", "b@example.org", "c@example.org"], actor=None)

    assert digest.enabled()
    assert digest.recipients() == ["B@example.org", "c@example.org"]
    assert DigestSetting.objects.count() == 2
    with pytest.raises(AppendOnlyError):
        DigestSetting.objects.update(enabled=False)


@pytest.mark.parametrize(
    ("on", "addresses", "text"),
    [
        (True, [], "Mindestens eine Adresse"),
        (True, ["keine-adresse"], "keine gültige E-Mail-Adresse"),
        (False, ["material@example.org", "kaputt@"], "keine gültige E-Mail-Adresse"),
        (True, [f"m{i}@example.org" for i in range(11)], "Höchstens 10 Adressen"),
    ],
)
def test_invalid_settings_are_rejected(on: bool, addresses: list[str], text: str) -> None:
    with pytest.raises(digest.DigestError, match=text):
        digest.configure(on, addresses, actor=None)

    assert not DigestSetting.objects.exists()


@pytest.mark.parametrize("role", [Role.PPE_EXPERT, Role.TRAINER, Role.LENDER, Role.VIEWER])
def test_only_admin_changes_settings(role: Role) -> None:
    with pytest.raises(PermissionDenied):
        digest.configure(True, TEAM, actor=make_inspector(role=role))
    with pytest.raises(PermissionDenied):
        digest.send_test(actor=make_inspector(role=role))


def test_test_mail_has_no_personal_data(
    switched_on: DigestSetting, make_part: MakePart, borrower: Borrower, lender: User
) -> None:
    lent(make_part, borrower, lender)

    count = digest.send_test(actor=make_inspector(role=Role.ADMIN))

    assert count == 2
    assert [message.to for message in mail.outbox] == [[TEAM[0]], [TEAM[1]]]
    assert all("Kim Beispiel" not in message.body for message in mail.outbox)
    assert "Testmail" in mail.outbox[0].subject
    assert not DigestRun.objects.exists()


def test_test_mail_needs_recipients() -> None:
    with pytest.raises(digest.DigestError, match="keine Empfänger"):
        digest.send_test(actor=None)


# --- Oberfläche ---


def test_admin_changes_settings_with_yubikey(client: Client) -> None:
    admin = make_inspector(role=Role.ADMIN)
    login_with_yubikey(client, admin)
    url = reverse("lending:digest_settings")

    assert client.get(url).status_code == 200
    response = client.post(
        url, {"an": "on", "empfaenger": "material@example.org\nvorstand@example.org"}
    )

    assert response.status_code == 302
    setting = DigestSetting.objects.get()
    assert setting.enabled and setting.recipients == TEAM and setting.actor == admin


def test_change_without_yubikey_is_refused(client: Client) -> None:
    client.force_login(make_inspector(role=Role.ADMIN))

    response = client.post(
        reverse("lending:digest_settings"), {"an": "on", "empfaenger": "material@example.org"}
    )

    assert response.status_code == 403
    assert not DigestSetting.objects.exists()


def test_invalid_address_is_shown_not_stored(client: Client) -> None:
    login_with_yubikey(client, make_inspector(role=Role.ADMIN))

    response = client.post(
        reverse("lending:digest_settings"), {"an": "on", "empfaenger": "keine-adresse"}
    )

    assert response.status_code == 200
    assert "keine gültige E-Mail-Adresse" in response.content.decode()
    assert not DigestSetting.objects.exists()


def test_test_mail_button(client: Client, switched_on: DigestSetting) -> None:
    client.force_login(make_inspector(role=Role.ADMIN))

    response = client.post(reverse("lending:digest_test"))

    assert response.status_code == 302
    assert len(mail.outbox) == 2


def test_overview_links_to_settings_for_admin_only(client: Client) -> None:
    client.force_login(make_inspector(role=Role.ADMIN))
    assert (
        reverse("lending:digest_settings")
        in client.get(reverse("lending:overview")).content.decode()
    )

    client.force_login(make_inspector(role=Role.LENDER))
    assert (
        reverse("lending:digest_settings")
        not in client.get(reverse("lending:overview")).content.decode()
    )


# --- Täglicher Befehl ---


def test_command_runs_digest_even_when_reminders_are_off(
    switched_on: DigestSetting, make_part: MakePart, borrower: Borrower, lender: User
) -> None:
    booking = lent(make_part, borrower, lender)
    reminders.set_enabled(False, actor=None)
    out = StringIO()

    call_command("erinnerungen", f"--stichtag={END + DAY:%Y-%m-%d}", stdout=out)

    text = out.getvalue()
    assert "Erinnerungen sind für alle ausgeschaltet." in text
    assert "Sammelmail: 1 Vorgang an 2 Adresse(n) gesendet." in text
    assert "Kim Beispiel" not in text and "example.org" not in text
    assert [message.to for message in mail.outbox] == [[TEAM[0]], [TEAM[1]]]
    assert f"#{booking.pk}" in mail.outbox[0].body


def test_command_dry_run_reports_digest(
    switched_on: DigestSetting, make_part: MakePart, borrower: Borrower, lender: User
) -> None:
    lent(make_part, borrower, lender)
    out = StringIO()

    call_command("erinnerungen", "--probelauf", f"--stichtag={END + DAY:%Y-%m-%d}", stdout=out)

    assert "Probelauf: Sammelmail mit 1 Vorgang an 2 Adresse(n)." in out.getvalue()
    assert mail.outbox == []


@override_settings(MAILERS=FAILING)
def test_command_fails_when_digest_fails(
    switched_on: DigestSetting, make_part: MakePart, borrower: Borrower, lender: User
) -> None:
    """Ein Fehler soll das Monitoring auslösen (Herzschlag des Timers, #24)."""
    lent(make_part, borrower, lender)

    with pytest.raises(CommandError, match="Sammelmail"):
        call_command("erinnerungen", f"--stichtag={END + DAY:%Y-%m-%d}", stdout=StringIO())
