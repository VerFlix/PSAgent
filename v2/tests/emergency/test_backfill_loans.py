"""Nacherfassung nach dem Notbetrieb, Teil 2: Ausgaben und Rückgaben (Parität 22).

Formular „Ausgabe und Rückgabe“ (A-…): Was auf Papier ausgegeben wurde, wird mit dem Datum
vom Formular eingetragen, auch wenn es gegen eine Regel verstieß (das Teil ist ja draußen);
solche Fälle prüft ein PSA-Prüfer auf der Klärungsliste. Nur eine Doppelbuchung lässt die
Datenbank nicht zu: Sie wartet auf der Klärungsliste, bis ein PSA-Prüfer die alte Reservierung
storniert und die Ausleihe übernimmt oder sie mit Begründung verwirft.
"""

from datetime import date, timedelta
from typing import Any

import pytest
from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role, User
from emergency import backfill
from emergency.binder import SHEETS_PER_BINDER
from emergency.models import Binder, Clarification, FormSheet, LoanForm, PaperForm
from inventory.models import EventKind
from inventory.services import record_event
from lending.models import Loan, LoanEvent, LoanEventKind, LoanStatus, SignatureOrigin
from lending.services import create_borrower, lend, reserve
from tests.helpers import make_inspector
from tests.lending.conftest import MakePart, checkout_kwargs

pytestmark = pytest.mark.django_db
SCAN = b"%PDF-1.4\n% Scan Ausgabeformular, kuenstliche Testdaten\n%%EOF\n"


@pytest.fixture(autouse=True)
def real_today(monkeypatch: pytest.MonkeyPatch) -> None:
    """Die Nacherfassung rechnet mit dem echten Datum (Mappe und Formular sind von heute)."""
    monkeypatch.setattr("lending.services.today", timezone.localdate)


@pytest.fixture
def make_part(make_part: MakePart, monkeypatch: pytest.MonkeyPatch) -> MakePart:
    """Teile, die schon vor 30 Tagen angelegt, geprüft und freigegeben wurden."""

    def make(**fields: Any) -> Any:
        past = timezone.now() - timedelta(days=30)
        with monkeypatch.context() as patch:
            patch.setattr(timezone, "now", lambda: past)
            return make_part(**fields)

    return make


@pytest.fixture
def expert() -> User:
    return make_inspector()


def days(n: int) -> date:
    return timezone.localdate() + timedelta(days=n)


def printed(days_ago: int = 10) -> Binder:
    binder = Binder.objects.create(
        created_at=timezone.now() - timedelta(days=days_ago), sha256="0" * 64
    )
    for kind, count in SHEETS_PER_BINDER.items():
        start = FormSheet.objects.filter(kind=kind).count()
        FormSheet.objects.bulk_create(
            FormSheet(binder=binder, kind=kind, number=start + n) for n in range(1, count + 1)
        )
    return binder


def checkout(*eis: str, **extra: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "eis": list(eis),
        "quick_check": True,
        "gal_provided": True,
        "issued_on": days(-5),
        "planned_return": days(5),
        "borrower_name": "Noa Papier",
        "address": "Teststraße 2, 00000 Beispielstadt",
        "phone": "0000 000000",
        "email": "",
        "issued_by_name": "Testperson Ausgabe",
    }
    data.update(extra)
    return data


def with_return(data: dict[str, Any], **extra: Any) -> dict[str, Any]:
    data.update(
        {
            "returned_on": days(-2),
            "return_quick_check": True,
            "return_comment": "harter Sturz am Klettersteig",
            "received_by_name": "Testperson Rücknahme",
        }
    )
    data.update(extra)
    return data


def ei(part: Any) -> str:
    return str(part.components.get().ei)


# --- Ausgabe ---


def test_parity_22_checkout_backfilled_with_original_dates(
    make_part: MakePart, lender: User
) -> None:
    printed()
    part = make_part()

    form = backfill.capture_loan_form(
        "A-00001", [(SCAN, "scan.pdf")], checkout(ei(part)), actor=lender
    )

    loan = Loan.objects.get(part=part)
    assert loan.status == LoanStatus.LENT
    assert (loan.start, loan.end) == (days(-5), days(5))
    assert loan.booking.borrower.display_name == "Noa Papier"
    event = loan.events.get(kind=LoanEventKind.CHECKED_OUT)
    assert timezone.localtime(event.at).date() == days(-5)
    assert event.actor == lender and event.actor_name == "Testperson Ausgabe"
    assert event.quick_check and event.gal_provided
    assert "Formular A-00001" in event.comment and "nachgetragen von" in event.comment
    assert event.signature is not None and event.signature.origin == SignatureOrigin.PAPER
    assert form.loan_form.result.booking == loan.booking
    assert backfill.open_cases() == []


def test_parity_22_checkout_and_return_on_same_form(make_part: MakePart, lender: User) -> None:
    printed()
    part = make_part()

    backfill.capture_loan_form(
        "A-00001", [(SCAN, "scan.pdf")], with_return(checkout(ei(part))), actor=lender
    )

    loan = Loan.objects.get(part=part)
    assert loan.status == LoanStatus.RETURNED
    returned = loan.events.get(kind=LoanEventKind.RETURNED)
    assert timezone.localtime(returned.at).date() == days(-2)
    assert returned.actor_name == "Testperson Rücknahme"
    assert "harter Sturz" in returned.comment


def test_parity_22_lock_on_return(make_part: MakePart, lender: User) -> None:
    printed()
    part = make_part()

    backfill.capture_loan_form(
        "A-00001",
        [(SCAN, "scan.pdf")],
        with_return(checkout(ei(part)), lock_reason="Naht an der Beinschlaufe offen"),
        actor=lender,
    )

    part.refresh_from_db()
    assert part.is_locked()
    assert "Formular A-00001" in part.events.get(kind=EventKind.LOCKED).comment


def test_parity_22_existing_borrower_is_reused(make_part: MakePart, lender: User) -> None:
    printed()
    known = create_borrower("Noa Papier", address="alt")
    part = make_part()

    backfill.capture_loan_form("A-00001", [(SCAN, "scan.pdf")], checkout(ei(part)), actor=lender)

    loan = Loan.objects.get(part=part)
    assert loan.booking.borrower == known
    known.contact.refresh_from_db()
    assert known.contact.address.startswith("Teststraße 2")


def test_parity_22_locked_part_is_recorded_and_checked(make_part: MakePart, lender: User) -> None:
    """Auf Papier trotz Sperre ausgegeben: Die Ausleihe steht im System, ein Prüfer sieht es."""
    printed()
    part = make_part()
    record_event(
        part,
        EventKind.LOCKED,
        actor=None,
        comment="vor dem Ausfall gesperrt",
        at=timezone.now() - timedelta(days=8),
    )

    form = backfill.capture_loan_form(
        "A-00001", [(SCAN, "scan.pdf")], checkout(ei(part)), actor=lender
    )

    assert Loan.objects.get(part=part).status == LoanStatus.LENT
    [case] = backfill.open_cases()
    assert case.key == f"ausgabe-pruefen:{form.loan_form.pk}"
    assert "gesperrt" in case.detail


def test_parity_22_missing_quick_check_and_contact_are_flagged(
    make_part: MakePart, lender: User
) -> None:
    printed()
    part = make_part()

    backfill.capture_loan_form(
        "A-00001",
        [(SCAN, "scan.pdf")],
        checkout(ei(part), quick_check=False, address="", phone=""),
        actor=lender,
    )

    [case] = backfill.open_cases()
    assert "Kurzkontrolle" in case.detail and "Adresse" in case.detail


def test_parity_22_double_booking_waits_for_expert(
    make_part: MakePart, lender: User, expert: User
) -> None:
    printed()
    part = make_part()
    reservation = reserve(create_borrower("Alex Vorher"), [part], days(-3), days(-1), actor=lender)

    form = backfill.capture_loan_form(
        "A-00001", [(SCAN, "scan.pdf")], checkout(ei(part)), actor=lender
    )

    assert not Loan.objects.filter(part=part, status=LoanStatus.LENT).exists()
    [case] = backfill.open_cases()
    assert case.key == f"ausgabe:{form.loan_form.pk}" and case.can_apply
    assert "Alex Vorher" not in case.detail  # keine Namen auf der Liste nötig

    backfill.apply_loan_form(form.loan_form, note="Reservierung war hinfällig", actor=expert)

    assert reservation.loans.get().status == LoanStatus.CANCELLED
    assert Loan.objects.get(part=part, status=LoanStatus.LENT).start == days(-5)
    assert backfill.open_cases() == []
    assert Clarification.objects.get().resolved_by == expert


def test_double_booking_can_be_discarded(make_part: MakePart, lender: User, expert: User) -> None:
    printed()
    part = make_part()
    reserve(create_borrower("Alex Vorher"), [part], days(-3), days(-1), actor=lender)
    form = backfill.capture_loan_form(
        "A-00001", [(SCAN, "scan.pdf")], checkout(ei(part)), actor=lender
    )

    backfill.resolve(f"ausgabe:{form.loan_form.pk}", note="Formular doppelt", actor=expert)

    assert backfill.open_cases() == []
    assert not Loan.objects.filter(part=part, status=LoanStatus.LENT).exists()


def test_overlap_with_running_loan_cannot_be_applied(
    make_part: MakePart, lender: User, expert: User
) -> None:
    """Nur Reservierungen storniert der Prüfer; eine laufende Ausleihe klärt er anders."""
    printed()
    part = make_part()
    lend(
        create_borrower("Alex Vorher", address="x", phone="1"),
        [part],
        days(-6),
        days(1),
        **checkout_kwargs(lender),
    )
    form = backfill.capture_loan_form(
        "A-00001", [(SCAN, "scan.pdf")], checkout(ei(part)), actor=lender
    )

    [case] = backfill.open_cases()
    assert not case.can_apply
    with pytest.raises(backfill.BackfillError, match="laufende Ausleihe"):
        backfill.apply_loan_form(form.loan_form, note="übernehmen", actor=expert)


@pytest.mark.parametrize("role", [Role.TRAINER, Role.LENDER])
def test_only_expert_applies_double_booking(role: Role, make_part: MakePart, lender: User) -> None:
    printed()
    part = make_part()
    reserve(create_borrower("Alex Vorher"), [part], days(-3), days(-1), actor=lender)
    form = backfill.capture_loan_form(
        "A-00001", [(SCAN, "scan.pdf")], checkout(ei(part)), actor=lender
    )

    with pytest.raises(PermissionDenied):
        backfill.apply_loan_form(
            form.loan_form, note="übernehmen", actor=make_inspector(role=role, qualified=False)
        )

    assert not Loan.objects.filter(part=part, status=LoanStatus.LENT).exists()


# --- Rückgabe ohne Ausgabe auf dem Formular ---


def test_parity_22_return_of_loan_from_before_outage(make_part: MakePart, lender: User) -> None:
    printed()
    part = make_part()
    lend(
        create_borrower("Kim Vorher", address="x", phone="1"),
        [part],
        days(-8),
        days(2),
        **checkout_kwargs(lender),
    )
    data = with_return({"eis": [ei(part)], "issued_on": None})

    backfill.capture_loan_form("A-00001", [(SCAN, "scan.pdf")], data, actor=lender)

    loan = Loan.objects.get(part=part)
    assert loan.status == LoanStatus.RETURNED
    assert timezone.localtime(loan.events.get(kind=LoanEventKind.RETURNED).at).date() == days(-2)
    assert backfill.open_cases() == []


def test_return_without_running_loan_is_flagged(make_part: MakePart, lender: User) -> None:
    printed()
    part = make_part()
    data = with_return({"eis": [ei(part)], "issued_on": None})

    form = backfill.capture_loan_form("A-00001", [(SCAN, "scan.pdf")], data, actor=lender)

    [case] = backfill.open_cases()
    assert case.key == f"ausgabe-pruefen:{form.loan_form.pk}"
    assert "keine laufende Ausleihe" in case.detail


# --- Eingaben ---


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"issued_on": None, "returned_on": None}, "Ausgabe oder Rückgabe"),
        ({"planned_return": None}, "Rückgabe geplant"),
        ({"borrower_name": " "}, "Namen des Entleihers"),
        ({"issued_on": days(1)}, "Zukunft"),
        ({"issued_on": days(-11)}, "vor dem Druck"),
        ({"eis": ["TST-GIBTSNICHT"]}, "TST-GIBTSNICHT"),
        ({"eis": []}, "mindestens ein Teil"),
    ],
)
def test_incomplete_form_is_refused(
    change: dict[str, Any], message: str, make_part: MakePart, lender: User
) -> None:
    printed()
    part = make_part()
    data = checkout(ei(part))
    data.update(change)
    data.setdefault("returned_on", None)

    with pytest.raises(backfill.BackfillError, match=message):
        backfill.capture_loan_form("A-00001", [(SCAN, "scan.pdf")], data, actor=lender)

    assert PaperForm.objects.count() == 0 and LoanForm.objects.count() == 0


def test_return_before_checkout_is_refused(make_part: MakePart, lender: User) -> None:
    printed()
    part = make_part()

    with pytest.raises(backfill.BackfillError, match="vor der Ausgabe"):
        backfill.capture_loan_form(
            "A-00001",
            [(SCAN, "scan.pdf")],
            with_return(checkout(ei(part)), returned_on=days(-6)),
            actor=lender,
        )


def test_viewer_cannot_backfill_loans(make_part: MakePart) -> None:
    """Verbotener Fall: Ausleihen nacherfassen erst ab Verleiher."""
    printed()
    part = make_part()

    with pytest.raises(PermissionDenied):
        backfill.capture_loan_form(
            "A-00001",
            [(SCAN, "scan.pdf")],
            checkout(ei(part)),
            actor=make_inspector(role=Role.VIEWER, qualified=False),
        )

    assert not LoanEvent.objects.exists()


# --- Oberfläche ---


def test_parity_22_loan_form_via_page(client: Client, lender: User, make_part: MakePart) -> None:
    printed()
    part = make_part()
    client.force_login(lender)

    page = client.get(reverse("emergency:backfill_loans")).content.decode()
    response = client.post(
        reverse("emergency:backfill_loans"),
        {
            "number": "A-00001",
            "scan": [SimpleUploadedFile("scan.pdf", SCAN, content_type="application/pdf")],
            "eis": ei(part),
            "quick_check": "on",
            "gal_provided": "on",
            "issued_on": days(-5).isoformat(),
            "planned_return": days(5).isoformat(),
            "borrower_name": "Noa Papier",
            "address": "Teststraße 2",
            "phone": "0000 000000",
            "issued_by_name": "Testperson Ausgabe",
        },
        follow=True,
    )

    assert "Teile (EI), je Zeile eine" in page
    assert page.count('name="quick_check"') == 1 and page.count('name="email"') == 1
    assert "Formular A-00001 nacherfasst" in response.content.decode()
    assert Loan.objects.get(part=part).status == LoanStatus.LENT


def test_parity_22_expert_applies_on_clarification_page(
    client: Client, lender: User, expert: User, make_part: MakePart
) -> None:
    printed()
    part = make_part()
    reserve(create_borrower("Alex Vorher"), [part], days(-3), days(-1), actor=lender)
    form = backfill.capture_loan_form(
        "A-00001", [(SCAN, "scan.pdf")], checkout(ei(part)), actor=lender
    )
    client.force_login(expert)

    page = client.get(reverse("emergency:clarifications")).content.decode()
    client.post(
        reverse("emergency:apply_loan_form", args=[form.loan_form.pk]),
        {"note": "Reservierung war hinfällig"},
    )

    assert "Reservierung stornieren und Ausleihe übernehmen" in page
    assert Loan.objects.get(part=part, status=LoanStatus.LENT)
