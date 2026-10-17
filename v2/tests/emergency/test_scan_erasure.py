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
from privacy.services import erase_borrower
from tests.emergency.test_backfill_loans import SCAN, checkout, ei, make_part, printed, real_today
from tests.helpers import make_inspector
from tests.lending.conftest import MakePart

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
