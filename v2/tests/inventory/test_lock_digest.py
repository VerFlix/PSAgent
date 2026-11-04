"""Tägliche Sammelmail zu neuen Sperren an Vereinsadressen (#141).

Festlegung (Felix, 04.11.2026): einmal täglich, nur wenn es neue Sperren gab; jede Sperre zählt
(PC, Theke, PSA-Prüfung); eigene Empfängerliste, getrennt von #128, gepflegt vom Admin mit
YubiKey; Testmail; Inhalt Teil, EI, Zeitpunkt, wer, Kommentar, Stand jetzt, Link; keine Daten des
Entleihers.
"""

from datetime import date, timedelta
from io import StringIO

import pytest
from django.core import mail
from django.core.exceptions import PermissionDenied
from django.core.management import CommandError, call_command
from django.test import Client, override_settings
from django.urls import reverse

from accounts.models import Role, User
from core.append_only import AppendOnlyError
from inspections.models import Outcome
from inventory import lock_digest
from inventory.models import EventKind, LockDigestRun, LockDigestSetting, Part, PartEvent
from inventory.services import create_product, link_document, record_event
from lending import digest
from lending.models import Borrower
from lending.services import create_borrower
from mailer.digests import DigestError
from tests.counter.test_returns import DAMAGE, lent_out, return_url, take_back
from tests.helpers import adopt_inspection, make_inspector
from tests.inventory.conftest import component
from tests.yubikey_client import login_with_yubikey

pytestmark = pytest.mark.django_db
TODAY = date(2026, 10, 15)
TOMORROW = TODAY + timedelta(days=1)
FAILING = {"default": {"BACKEND": "tests.lending.test_receipts.FailingBackend"}}
TEAM = ["psa@example.org", "materialwart@example.org"]


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fester Stichtag für Ausleihen, wie in den Tests der Theke."""
    monkeypatch.setattr("lending.services.today", lambda: TODAY)


@pytest.fixture
def lender(db: None) -> User:
    return User.objects.create_user(
        username="theke", first_name="Testperson", last_name="Theke", role=Role.LENDER
    )


@pytest.fixture
def borrower(db: None) -> Borrower:
    return create_borrower(
        "Kim Beispiel",
        address="Teststraße 1, 00000 Beispielstadt",
        email="kim.beispiel@example.org",
        phone="0000 000000",
    )


def released(ei: str, designation: str = "Klettergurt") -> Part:
    part = create_product(component(ei, designation=designation))
    part.documents.add(link_document(f"https://example.org/gal/{ei}.pdf", title=f"GAL {ei}"))
    adopt_inspection([part], next_due=TODAY + timedelta(days=365))
    return part


def lock(part: Part, actor: User, comment: str = "Naht aufgescheuert") -> PartEvent:
    return record_event(part, EventKind.LOCKED, actor=actor, comment=comment)


@pytest.fixture
def switched_on(db: None) -> LockDigestSetting:
    return lock_digest.configure(True, TEAM, actor=None)


# --- Inhalt ---


@override_settings(PSAGENT_BASE_URL="https://psagent.example.ts.net")
def test_lock_at_the_pc_is_listed_with_part_ei_actor_comment_and_link(
    switched_on: LockDigestSetting, lender: User
) -> None:
    rope = released("DAV-HL-S-21", "Einfachseil 50 m")
    event = lock(rope, lender, "Mantel verfärbt\nbitte prüfen")

    result = lock_digest.run(TOMORROW)

    assert result.sent and result.locks == 1 and result.recipients == 2
    first, second = mail.outbox
    assert first.to == [TEAM[0]] and second.to == [TEAM[1]]
    assert first.subject == f"PSAgent: 1 neue Sperre ({TOMORROW:%d.%m.%Y})"
    body = first.body
    assert f"- {rope.name} (EI DAV-HL-S-21)" in body
    assert "von Testperson Theke" in body
    assert "Kommentar: Mantel verfärbt\n    bitte prüfen" in body
    assert "Stand jetzt: gesperrt" in body
    assert f"https://psagent.example.ts.net/teile/{rope.pk}/" in body
    assert "aus einer PSA-Prüfung" not in body
    run = LockDigestRun.objects.get()
    assert (run.day, run.last_event, run.locks, run.recipients) == (TOMORROW, event, 1, 2)


def test_lock_at_the_counter_sends_exactly_one_mail_per_recipient_without_borrower_data(
    switched_on: LockDigestSetting, client: Client, lender: User, borrower: Borrower
) -> None:
    """Abnahme #141: Sperre an der Theke, genau eine Mail, keine Kontaktdaten des Entleihers."""
    helmet = released("DAV-HL-H-1", "Kletterhelm")
    booking = lent_out(borrower, [helmet], 7)
    client.force_login(lender)
    loan = booking.loans.get()
    client.post(
        return_url(booking),
        take_back(zurueck=[loan.pk], schaden=[loan.pk], reason="Sturz belastet"),
    )
    assert helmet.is_locked()

    lock_digest.run(TOMORROW)

    assert [message.to for message in mail.outbox] == [[TEAM[0]], [TEAM[1]]]
    body = mail.outbox[0].body
    assert f"{helmet.name} (EI DAV-HL-H-1)" in body and "Kommentar: Sturz belastet" in body
    for private in ("Kim Beispiel", "kim.beispiel@example.org", "Teststraße", "0000 000000"):
        assert private not in body
    assert f"#{booking.pk}" not in body


def test_report_damage_at_the_counter_is_listed(
    switched_on: LockDigestSetting, client: Client, lender: User
) -> None:
    rope = released("DAV-HL-S-22", "Einfachseil 60 m")
    client.force_login(lender)
    client.post(
        DAMAGE,
        {
            "sperren": "1",
            "teil": rope.pk,
            "teil_ei": "DAV-HL-S-22",
            "reason": "Chemikalien / Hitze",
            "comment": "Mantel verfärbt",
        },
    )

    lock_digest.run(TOMORROW)

    assert "Kommentar: Chemikalien / Hitze: Mantel verfärbt" in mail.outbox[0].body


def test_lock_from_an_inspection_is_marked(switched_on: LockDigestSetting) -> None:
    belt = released("DAV-HL-G-7")
    adopt_inspection(
        [belt], next_due=TODAY + timedelta(days=365), outcome=Outcome.DEFECT, comment="Naht offen"
    )

    lock_digest.run(TOMORROW)

    body = mail.outbox[0].body
    assert "aus einer PSA-Prüfung" in body and "Kommentar: Naht offen" in body


def test_status_now_shows_a_later_unlock(switched_on: LockDigestSetting, lender: User) -> None:
    belt = released("DAV-HL-G-8")
    lock(belt, lender)
    record_event(belt, EventKind.UNLOCKED, actor=make_inspector(), comment="geprüft, in Ordnung")

    lock_digest.run(TOMORROW)

    assert "Stand jetzt: freigegeben" in mail.outbox[0].body


# --- Was „neu“ ist ---


def test_next_mail_lists_only_locks_after_the_last_one(
    switched_on: LockDigestSetting, lender: User
) -> None:
    first, second = released("DAV-HL-G-1"), released("DAV-HL-G-2")
    lock(first, lender)
    lock_digest.run(TOMORROW)
    lock(second, lender)

    result = lock_digest.run(TOMORROW + timedelta(days=1))

    assert result.locks == 1
    body = mail.outbox[-1].body
    assert "DAV-HL-G-2" in body and "DAV-HL-G-1" not in body
    assert "Neu erfasste Sperren seit " in body


def test_backdated_lock_entered_later_still_counts(
    switched_on: LockDigestSetting, lender: User
) -> None:
    """Nacherfassung nach dem Notbetrieb: Zeitpunkt zurück, aber nach der letzten Mail erfasst."""
    first, second = released("DAV-HL-G-3"), released("DAV-HL-G-4")
    lock(first, lender)
    lock_digest.run(TOMORROW)
    earlier = PartEvent.objects.get(part=first, kind=EventKind.LOCKED).at - timedelta(days=2)
    record_event(second, EventKind.LOCKED, actor=lender, comment="Papier: Riss", at=earlier)

    result = lock_digest.run(TOMORROW + timedelta(days=1))

    assert result.sent and result.locks == 1 and "DAV-HL-G-4" in mail.outbox[-1].body


def test_first_mail_starts_when_switched_on(lender: User) -> None:
    lock(released("DAV-HL-G-5"), lender)
    lock_digest.configure(True, TEAM, actor=None)

    assert lock_digest.run(TOMORROW).reason == "keine neuen Sperren"
    lock(released("DAV-HL-G-6"), lender)
    assert lock_digest.run(TOMORROW).locks == 1


def test_nothing_new_sends_nothing(switched_on: LockDigestSetting) -> None:
    result = lock_digest.run(TOMORROW)

    assert not result.sent and result.reason == "keine neuen Sperren"
    assert mail.outbox == [] and not LockDigestRun.objects.exists()


def test_switched_off_or_never_set_sends_nothing(lender: User) -> None:
    lock(released("DAV-HL-G-9"), lender)
    assert lock_digest.run(TOMORROW).reason == "aus"

    lock_digest.configure(True, TEAM, actor=None)
    lock_digest.configure(False, TEAM, actor=None)
    assert lock_digest.run(TOMORROW).reason == "aus"
    assert mail.outbox == []


def test_at_most_one_mail_per_day(switched_on: LockDigestSetting, lender: User) -> None:
    lock(released("DAV-HL-G-10"), lender)
    lock_digest.run(TOMORROW)
    lock(released("DAV-HL-G-11"), lender)

    assert lock_digest.run(TOMORROW).reason == "heute schon gesendet"
    assert len(mail.outbox) == 2


def test_dry_run_neither_sends_nor_records(switched_on: LockDigestSetting, lender: User) -> None:
    lock(released("DAV-HL-G-12"), lender)

    result = lock_digest.run(TOMORROW, dry_run=True)

    assert result.locks == 1 and not result.sent
    assert mail.outbox == [] and not LockDigestRun.objects.exists()


@override_settings(MAILERS=FAILING)
def test_failed_send_is_not_recorded_and_raises(
    switched_on: LockDigestSetting, lender: User
) -> None:
    lock(released("DAV-HL-G-13"), lender)

    with pytest.raises(DigestError):
        lock_digest.run(TOMORROW)

    assert not LockDigestRun.objects.exists()


def test_overdue_digest_does_not_send_lock_mails(lender: User) -> None:
    """Getrennte Listen: Empfänger von #128 bekommen keine Sperren und umgekehrt."""
    digest.configure(True, ["vorstand@example.org"], actor=None)
    lock(released("DAV-HL-G-14"), lender)

    assert lock_digest.run(TOMORROW).reason == "aus"
    assert digest.run(TOMORROW).reason == "nichts überfällig"
    assert mail.outbox == []


# --- Einstellungen ---


def test_settings_are_appended_and_the_latest_counts() -> None:
    lock_digest.configure(True, ["a@example.org"], actor=None)
    lock_digest.configure(True, [" B@example.org ", "b@example.org"], actor=None)

    assert lock_digest.enabled() and lock_digest.recipients() == ["B@example.org"]
    assert LockDigestSetting.objects.count() == 2
    assert not digest.recipients()
    with pytest.raises(AppendOnlyError):
        LockDigestSetting.objects.update(enabled=False)


@pytest.mark.parametrize(
    ("on", "addresses", "text"),
    [
        (True, [], "Mindestens eine Adresse"),
        (True, ["keine-adresse"], "keine gültige E-Mail-Adresse"),
        (True, [f"m{i}@example.org" for i in range(11)], "Höchstens 10 Adressen"),
    ],
)
def test_invalid_settings_are_rejected(on: bool, addresses: list[str], text: str) -> None:
    with pytest.raises(DigestError, match=text):
        lock_digest.configure(on, addresses, actor=None)

    assert not LockDigestSetting.objects.exists()


@pytest.mark.parametrize("role", [Role.PPE_EXPERT, Role.TRAINER, Role.LENDER, Role.VIEWER])
def test_only_admin_changes_settings(role: Role) -> None:
    with pytest.raises(PermissionDenied):
        lock_digest.configure(True, TEAM, actor=make_inspector(role=role))
    with pytest.raises(PermissionDenied):
        lock_digest.send_test(actor=make_inspector(role=role))


def test_test_mail_has_no_parts_or_names(switched_on: LockDigestSetting, lender: User) -> None:
    lock(released("DAV-HL-G-15"), lender)

    count = lock_digest.send_test(actor=make_inspector(role=Role.ADMIN))

    assert count == 2 and "Testmail" in mail.outbox[0].subject
    assert all("DAV-HL-G-15" not in m.body and "Testperson" not in m.body for m in mail.outbox)
    assert not LockDigestRun.objects.exists()


def test_test_mail_needs_recipients() -> None:
    with pytest.raises(DigestError, match="keine Empfänger"):
        lock_digest.send_test(actor=None)


# --- Oberfläche ---

SETTINGS = reverse("inventory:lock_digest_settings")


def test_admin_changes_settings_with_yubikey(client: Client) -> None:
    admin = make_inspector(role=Role.ADMIN)
    login_with_yubikey(client, admin)

    assert client.get(SETTINGS).status_code == 200
    response = client.post(SETTINGS, {"an": "on", "empfaenger": "\n".join(TEAM)})

    assert response.status_code == 302
    setting = LockDigestSetting.objects.get()
    assert setting.enabled and setting.recipients == TEAM and setting.actor == admin


def test_change_without_yubikey_is_refused(client: Client) -> None:
    client.force_login(make_inspector(role=Role.ADMIN))

    response = client.post(SETTINGS, {"an": "on", "empfaenger": TEAM[0]})

    assert response.status_code == 403
    assert not LockDigestSetting.objects.exists()


def test_invalid_address_is_shown_not_stored(client: Client) -> None:
    login_with_yubikey(client, make_inspector(role=Role.ADMIN))

    response = client.post(SETTINGS, {"an": "on", "empfaenger": "keine-adresse"})

    assert response.status_code == 200
    assert "keine gültige E-Mail-Adresse" in response.content.decode()
    assert not LockDigestSetting.objects.exists()


def test_test_mail_button(client: Client, switched_on: LockDigestSetting) -> None:
    client.force_login(make_inspector(role=Role.ADMIN))

    response = client.post(reverse("inventory:lock_digest_test"))

    assert response.status_code == 302 and len(mail.outbox) == 2


def test_part_list_links_to_settings_for_admin_only(client: Client) -> None:
    client.force_login(make_inspector(role=Role.ADMIN))
    assert SETTINGS in client.get(reverse("inventory:part_list")).content.decode()

    client.force_login(make_inspector(role=Role.PPE_EXPERT))
    assert SETTINGS not in client.get(reverse("inventory:part_list")).content.decode()


# --- Täglicher Befehl ---


def test_command_sends_lock_digest(switched_on: LockDigestSetting, lender: User) -> None:
    lock(released("DAV-HL-G-16"), lender)
    out = StringIO()

    call_command("erinnerungen", f"--stichtag={TOMORROW:%Y-%m-%d}", stdout=out)

    text = out.getvalue()
    assert "Sammelmail zu Sperren: 1 Sperre(n) an 2 Adresse(n) gesendet." in text
    assert "Sammelmail: nicht gesendet (aus)." in text
    assert "DAV-HL-G-16" not in text and "example.org" not in text and "Testperson" not in text


def test_command_dry_run_reports_lock_digest(switched_on: LockDigestSetting, lender: User) -> None:
    lock(released("DAV-HL-G-17"), lender)
    out = StringIO()

    call_command("erinnerungen", "--probelauf", f"--stichtag={TOMORROW:%Y-%m-%d}", stdout=out)

    assert "Probelauf: Sammelmail zu Sperren mit 1 Sperre(n) an 2 Adresse(n)." in out.getvalue()
    assert mail.outbox == []


@override_settings(MAILERS=FAILING)
def test_command_fails_when_lock_digest_fails(switched_on: LockDigestSetting, lender: User) -> None:
    """Ein Fehler soll das Monitoring auslösen (Herzschlag des Timers, #24)."""
    lock(released("DAV-HL-G-18"), lender)

    with pytest.raises(CommandError, match="Sammelmail zu Sperren"):
        call_command("erinnerungen", f"--stichtag={TOMORROW:%Y-%m-%d}", stdout=StringIO())


def test_command_sends_lock_digest_even_when_overdue_digest_fails(
    switched_on: LockDigestSetting, lender: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*args: object, **kwargs: object) -> None:
        raise DigestError("Sammelmail an 1 von 1 Adresse(n) gescheitert.")

    monkeypatch.setattr("lending.digest.run", broken)
    lock(released("DAV-HL-G-19"), lender)

    with pytest.raises(CommandError, match=r"^Sammelmail: Sammelmail an 1 von 1"):
        call_command("erinnerungen", f"--stichtag={TOMORROW:%Y-%m-%d}", stdout=StringIO())

    assert len(mail.outbox) == 2 and LockDigestRun.objects.exists()
