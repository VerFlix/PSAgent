"""Löschfrist (#63): Personendaten gehen 3 Jahre nach Ende des Jahres der letzten Aktivität.

Beispiel: letzte Rückgabe im Mai 2026, gelöscht am 1.1.2030. Nie gelöscht wird bei offener
Ausleihe oder Löschsperre (Art. 17 Abs. 3 lit. e, Art. 18 DSGVO). Der tägliche Lauf
(`manage.py loeschfristen`) nennt Entleiher nur mit Nummer, nie mit Namen.
"""

from datetime import date
from io import StringIO

import pytest
from django.core.exceptions import PermissionDenied
from django.core.management import CommandError, call_command
from django.test import override_settings
from django.utils import timezone

from accounts.models import Role, User
from core.append_only import AppendOnlyError
from lending.models import Borrower, BorrowerContact
from lending.services import create_borrower, lend, reserve, return_loans
from privacy import services
from privacy.models import Erasure, ErasureHold
from privacy.services import PrivacyError
from tests.helpers import make_inspector
from tests.lending.conftest import DAY, TODAY, MakePart, checkout_kwargs, return_kwargs

pytestmark = pytest.mark.django_db
YEAR = timezone.localdate().year  # Ausgaben und Rückgaben tragen das echte Datum
DUE = date(YEAR + 4, 1, 1)
NAME = "Quirin Fristprobe"


def returned(make_part: MakePart, lender: User, name: str = NAME) -> Borrower:
    borrower = create_borrower(name, address="Teststraße 9", phone="0000 9")
    booking = lend(borrower, [make_part()], TODAY, TODAY + DAY, **checkout_kwargs(lender))
    return_loans(list(booking.loans.all()), **return_kwargs(lender))
    return borrower


def erased(borrower: Borrower) -> bool:
    return not BorrowerContact.objects.filter(borrower=borrower).exists()


# --- Frist ---


@pytest.mark.parametrize(
    ("years", "last", "expected"),
    [
        (3, date(2026, 5, 10), date(2030, 1, 1)),
        (3, date(2026, 12, 31), date(2030, 1, 1)),
        (3, date(2027, 1, 1), date(2031, 1, 1)),
        (1, date(2026, 5, 10), date(2028, 1, 1)),
    ],
)
def test_erasure_date(years: int, last: date, expected: date) -> None:
    with override_settings(PSAGENT_RETENTION_YEARS=years):
        assert services.erasure_date(last) == expected


def test_due_only_after_the_period(make_part: MakePart, lender: User) -> None:
    borrower = returned(make_part, lender)

    services.run_retention(DUE - DAY)
    assert not erased(borrower)

    services.run_retention(DUE)
    assert erased(borrower)
    erasure = Erasure.objects.get(borrower=borrower)
    assert erasure.by_deadline and erasure.actor is None
    assert erasure.last_activity.year == YEAR


def test_borrower_without_bookings_counts_from_creation(db: None) -> None:
    borrower = create_borrower(NAME)

    services.run_retention(DUE)

    assert erased(borrower)


@pytest.mark.parametrize("state", ["reserviert", "ausgeliehen"])
def test_open_loan_blocks_erasure(state: str, make_part: MakePart, lender: User) -> None:
    borrower = create_borrower(NAME, address="Teststraße 9", phone="0000 9")
    if state == "reserviert":
        reserve(borrower, [make_part()], TODAY, TODAY + DAY, actor=lender)
    else:
        lend(borrower, [make_part()], TODAY, TODAY + DAY, **checkout_kwargs(lender))

    services.run_retention(date(YEAR + 30, 1, 1))

    assert not erased(borrower)
    with pytest.raises(PrivacyError, match="offene Ausleihen"):
        services.erase_borrower(borrower, actor=None)


def test_hold_blocks_erasure_until_released(make_part: MakePart, lender: User) -> None:
    borrower = returned(make_part, lender)
    expert = make_inspector()
    services.set_hold(borrower, "Schadensfall Klettersteig, Anwalt eingeschaltet", actor=expert)

    services.run_retention(DUE)
    assert not erased(borrower)
    with pytest.raises(PrivacyError, match="Löschsperre"):
        services.erase_borrower(borrower, actor=None)

    services.release_hold(borrower, "Verfahren abgeschlossen", actor=expert)
    services.run_retention(DUE)
    assert erased(borrower)


def test_dry_run_changes_nothing(make_part: MakePart, lender: User) -> None:
    borrower = returned(make_part, lender)

    due = services.run_retention(DUE, dry_run=True)

    assert [entry.borrower for entry in due] == [borrower]
    assert not erased(borrower) and not Erasure.objects.exists()


def test_each_borrower_is_erased_once(make_part: MakePart, lender: User) -> None:
    returned(make_part, lender)

    first = services.run_retention(DUE)
    second = services.run_retention(DUE + 365 * DAY)

    assert len(first) == 1 and second == []
    assert Erasure.objects.count() == 1


def test_manual_erasure_is_logged(make_part: MakePart, lender: User) -> None:
    borrower = returned(make_part, lender)
    admin = make_inspector(role=Role.ADMIN, qualified=False)

    services.erase_borrower(borrower, actor=admin)

    erasure = Erasure.objects.get(borrower=borrower)
    assert not erasure.by_deadline and erasure.actor == admin
    with pytest.raises(PrivacyError, match="bereits gelöscht"):
        services.erase_borrower(borrower, actor=admin)


# --- Löschsperre ---


@pytest.mark.parametrize("role", [Role.ADMIN, Role.PPE_EXPERT])
def test_hold_by_admin_and_ppe_expert(role: Role, make_part: MakePart, lender: User) -> None:
    borrower = returned(make_part, lender)
    actor = make_inspector(role=role, qualified=False)

    services.set_hold(borrower, "Schadensfall", actor=actor)

    hold = ErasureHold.objects.get()
    assert hold.active and hold.actor == actor and hold.reason == "Schadensfall"
    assert services.hold_of(borrower) == hold


@pytest.mark.parametrize("role", [Role.TRAINER, Role.LENDER, Role.VIEWER])
def test_hold_forbidden_below_ppe_expert(role: Role, make_part: MakePart, lender: User) -> None:
    """Verbotener Fall: Setzen und Aufheben nur ab PSA-Sachkundig; nichts ändert sich."""
    borrower = returned(make_part, lender)
    services.set_hold(borrower, "Schadensfall", actor=None)
    actor = make_inspector(role=role, qualified=False)

    with pytest.raises(PermissionDenied, match="ab der Rolle PSA-Sachkundig"):
        services.set_hold(borrower, "anderer Grund", actor=actor)
    with pytest.raises(PermissionDenied, match="ab der Rolle PSA-Sachkundig"):
        services.release_hold(borrower, "aufheben", actor=actor)

    assert ErasureHold.objects.count() == 1
    assert services.hold_of(borrower) is not None


@pytest.mark.parametrize("reason", ["", "   "])
def test_hold_needs_a_reason(reason: str, make_part: MakePart, lender: User) -> None:
    borrower = returned(make_part, lender)

    with pytest.raises(PrivacyError, match="Grund"):
        services.set_hold(borrower, reason, actor=None)

    assert not ErasureHold.objects.exists()


def test_release_needs_an_active_hold(make_part: MakePart, lender: User) -> None:
    borrower = returned(make_part, lender)

    with pytest.raises(PrivacyError, match="keine Löschsperre"):
        services.release_hold(borrower, "aufheben", actor=None)


def test_hold_and_erasure_log_are_append_only(make_part: MakePart, lender: User) -> None:
    borrower = returned(make_part, lender)
    services.set_hold(borrower, "Schadensfall", actor=None)
    services.release_hold(borrower, "erledigt", actor=None)
    services.erase_borrower(borrower, actor=None)

    with pytest.raises(AppendOnlyError):
        ErasureHold.objects.filter(borrower=borrower).update(reason="geändert")
    with pytest.raises(AppendOnlyError):
        Erasure.objects.filter(borrower=borrower).delete()


# --- Befehl ---


def test_command_names_numbers_only(make_part: MakePart, lender: User) -> None:
    borrower = returned(make_part, lender)
    out = StringIO()

    call_command("loeschfristen", "--probelauf", f"--stichtag={DUE:%Y-%m-%d}", stdout=out)

    assert f"Entleiher #{borrower.pk}" in out.getvalue() and NAME not in out.getvalue()
    assert "Probelauf" in out.getvalue() and not erased(borrower)

    call_command("loeschfristen", f"--stichtag={DUE:%Y-%m-%d}", stdout=out)

    assert erased(borrower)
    assert "1 Entleiher gelöscht" in out.getvalue()


def test_command_erases_one_on_request(make_part: MakePart, lender: User) -> None:
    """Löschen auf Antrag (Art. 17 DSGVO), vor Ablauf der Frist."""
    borrower = returned(make_part, lender)
    out = StringIO()

    call_command("loeschfristen", f"--entleiher={borrower.pk}", stdout=out)

    assert erased(borrower) and not Erasure.objects.get(borrower=borrower).by_deadline
    assert f"Entleiher #{borrower.pk} gelöscht." in out.getvalue()


def test_command_refuses_blocked_borrower(make_part: MakePart, lender: User) -> None:
    borrower = create_borrower(NAME, address="Teststraße 9", phone="0000 9")
    lend(borrower, [make_part()], TODAY, TODAY + DAY, **checkout_kwargs(lender))

    with pytest.raises(CommandError, match="offene Ausleihen"):
        call_command("loeschfristen", f"--entleiher={borrower.pk}", stdout=StringIO())
    with pytest.raises(CommandError, match="gibt es nicht"):
        call_command("loeschfristen", "--entleiher=999999", stdout=StringIO())

    assert not erased(borrower)


def test_retention_years_come_from_the_environment() -> None:
    from django.conf import settings

    assert settings.PSAGENT_RETENTION_YEARS == 3
