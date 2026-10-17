"""Entleiher löschen (#63): Der Scan seines Ausleihformulars geht mit, die Prüfsumme bleibt.

Auf dem Scan stehen Name, Kontaktdaten und Unterschrift. Das Formular bleibt als Eintrag mit
der Prüfsumme des Scans erhalten; das Papieroriginal vernichtet der Verein (Runbook).
"""

import hashlib
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse

from accounts.models import User
from emergency import backfill
from emergency.models import PaperForm
from lending.models import Borrower
from lending.services import create_borrower, lend
from privacy.services import erase_borrower
from tests.emergency.test_backfill_loans import (
    SCAN,
    checkout,
    days,
    ei,
    make_part,
    printed,
    real_today,
    with_return,
)
from tests.helpers import make_inspector
from tests.lending.conftest import MakePart, checkout_kwargs

__all__ = ["make_part", "real_today"]
pytestmark = pytest.mark.django_db
OTHER_SCAN = b"%PDF-1.4\n% anderer Scan, kuenstliche Testdaten\n%%EOF\n"


def capture(number: str, scan: bytes, part: Any, actor: User, **extra: Any) -> PaperForm:
    return backfill.capture_loan_form(
        number, [(scan, "scan.pdf")], checkout(ei(part), **extra), actor=actor
    )


def test_scan_of_erased_borrower_is_deleted(
    make_part: MakePart, lender: User, django_capture_on_commit_callbacks: Any
) -> None:
    printed()
    form = capture("A-00001", SCAN, make_part(), lender)
    other = capture("A-00002", OTHER_SCAN, make_part(), lender, borrower_name="Kai Anders")
    borrower = form.loan_form.borrower
    assert borrower is not None

    with django_capture_on_commit_callbacks(execute=True):
        erase_borrower(borrower, actor=None)

    assert not backfill.scan_path(form).exists()
    assert backfill.scan_path(other).exists()
    form.refresh_from_db()
    assert form.scan_sha256 == hashlib.sha256(SCAN).hexdigest()


def test_shared_scan_file_stays_for_others(
    make_part: MakePart, lender: User, django_capture_on_commit_callbacks: Any
) -> None:
    """Gleicher Inhalt, eine Datei: Sie bleibt, solange ein anderes Formular sie braucht."""
    printed()
    form = capture("A-00001", SCAN, make_part(), lender)
    capture("A-00002", SCAN, make_part(), lender, borrower_name="Kai Anders")

    with django_capture_on_commit_callbacks(execute=True):
        erase_borrower(form.loan_form.borrower, actor=None)  # type: ignore[arg-type]

    assert backfill.scan_path(form).exists()


def test_deleted_scan_is_not_found(
    client: Client, make_part: MakePart, lender: User, django_capture_on_commit_callbacks: Any
) -> None:
    printed()
    form = capture("A-00001", SCAN, make_part(), lender)
    with django_capture_on_commit_callbacks(execute=True):
        erase_borrower(form.loan_form.borrower, actor=None)  # type: ignore[arg-type]
    client.force_login(make_inspector())

    response = client.get(reverse("emergency:scan", args=[form.pk]))

    assert response.status_code == 404


def lent_before_outage(part: Any, name: str, lender: User) -> Borrower:
    borrower = create_borrower(name, address="Teststraße 3", phone="0000 3")
    lend(borrower, [part], days(-8), days(2), **checkout_kwargs(lender))
    return borrower


def return_only(number: str, parts: list[Any], actor: User) -> PaperForm:
    data = with_return({"eis": [ei(part) for part in parts], "issued_on": None})
    return backfill.capture_loan_form(number, [(SCAN, "scan.pdf")], data, actor=actor)


def test_scan_of_return_only_form_is_deleted(
    make_part: MakePart, lender: User, django_capture_on_commit_callbacks: Any
) -> None:
    """Auf einer reinen Rückgabe steht kein Entleiher im Formular, aber seine Unterschrift."""
    printed()
    part = make_part()
    borrower = lent_before_outage(part, "Kim Vorher", lender)
    form = return_only("A-00001", [part], lender)

    with django_capture_on_commit_callbacks(execute=True):
        erase_borrower(borrower, actor=None)

    assert not backfill.scan_path(form).exists()


def test_scan_with_two_borrowers_goes_with_the_last(
    make_part: MakePart, lender: User, django_capture_on_commit_callbacks: Any
) -> None:
    printed()
    first, second = make_part(), make_part()
    kim = lent_before_outage(first, "Kim Vorher", lender)
    kai = lent_before_outage(second, "Kai Vorher", lender)
    form = return_only("A-00001", [first, second], lender)

    with django_capture_on_commit_callbacks(execute=True):
        erase_borrower(kim, actor=None)
    assert backfill.scan_path(form).exists()

    with django_capture_on_commit_callbacks(execute=True):
        erase_borrower(kai, actor=None)
    assert not backfill.scan_path(form).exists()
