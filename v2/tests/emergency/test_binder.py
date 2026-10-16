"""Notfallmappe mit Papierformularen (Parität 22, Konzept Abschnitt 7).

Die Mappe enthält alle Teile mit EI, Bezeichnung, Sperrstatus, nächster Prüfung und
GAL-Verweis, laufende Ausleihen und Reservierungen der nächsten vier Wochen (Entleiher nur mit
Namen) und einen Satz leerer Formulare mit fortlaufender Formularnummer. Jede Seite trägt das
Erstellungsdatum.
"""

import hashlib
import io
import re

import pytest
from django.core.management import call_command
from django.test import Client
from django.urls import reverse
from pypdf import PdfReader

from accounts.models import Role, User
from emergency import binder as service
from emergency.models import Binder, FormKind, FormSheet
from inventory.models import EventKind
from inventory.services import create_product, record_event
from lending.models import Borrower
from lending.services import create_borrower, lend, reserve
from tests.helpers import make_inspector
from tests.lending.conftest import DAY, TODAY, MakePart, checkout_kwargs

pytestmark = pytest.mark.django_db


def pages(pdf: bytes) -> list[str]:
    reader = PdfReader(io.BytesIO(pdf))
    return [re.sub(r"\s+", " ", page.extract_text()) for page in reader.pages]


def text_of(pdf: bytes) -> str:
    return " ".join(pages(pdf))


def pdf_of(binder: Binder) -> bytes:
    return service.path_of(binder).read_bytes()


# --- Inhalt ---


def test_parity_22_binder_lists_parts(make_part: MakePart) -> None:
    released = make_part(designation="Klettergurt Alpin")
    locked = make_part(designation="Helm Test")
    record_event(locked, EventKind.LOCKED, actor=None, comment="Riss in der Schale")
    retired = make_part(designation="Seil alt")
    record_event(retired, EventKind.RETIRED, actor=None, comment="Kern beschädigt")

    text = text_of(pdf_of(service.create_binder(actor=None)))

    assert released.components.get().ei in text and "Klettergurt Alpin" in text
    assert "15.10.2027" in text  # nächste Prüfung
    assert "GAL Testmodell" in text
    assert "gesperrt" in text and "Helm Test" in text
    assert retired.components.get().ei not in text


def test_parity_22_binder_lists_loans_of_next_four_weeks(
    make_part: MakePart, borrower: Borrower, lender: User
) -> None:
    lent = make_part(designation="Gurt verliehen")
    soon = make_part(designation="Gurt bald")
    later = make_part(designation="Gurt später")
    lend(borrower, [lent], TODAY - DAY, TODAY + 3 * DAY, **checkout_kwargs(lender))
    reserve(create_borrower("Noa Probe"), [soon], TODAY + 27 * DAY, TODAY + 30 * DAY, actor=lender)
    reserve(
        create_borrower("Alex Später"), [later], TODAY + 29 * DAY, TODAY + 30 * DAY, actor=lender
    )

    text = text_of(pdf_of(service.create_binder(actor=None)))
    loans = text.split("Laufende Ausleihen und Reservierungen")[1]

    assert "Kim Beispiel" in loans and "ausgeliehen" in loans and "18.10.2026" in loans
    assert "Noa Probe" in loans and "reserviert" in loans
    assert "Alex Später" not in text


def test_parity_22_binder_has_names_but_no_contact_data(
    make_part: MakePart, borrower: Borrower, lender: User
) -> None:
    """Die Offline-Kopie liegt auf Vereinsgeräten: Entleiher nur mit Namen."""
    lend(borrower, [make_part()], TODAY, TODAY + DAY, **checkout_kwargs(lender))

    text = text_of(pdf_of(service.create_binder(actor=None)))

    assert "Kim Beispiel" in text
    assert "Teststraße" not in text and "example.org" not in text.replace("example.org/gal", "")


def test_parity_22_forms_with_sequential_numbers(make_part: MakePart) -> None:
    make_part()

    first = service.create_binder(actor=None)
    second = service.create_binder(actor=None)

    loan_numbers = list(
        FormSheet.objects.filter(kind=FormKind.LOAN)
        .order_by("number")
        .values_list("binder_id", "number")
    )
    per_binder = service.SHEETS_PER_BINDER[FormKind.LOAN]
    assert loan_numbers == [(first.pk, n) for n in range(1, per_binder + 1)] + [
        (second.pk, n) for n in range(per_binder + 1, 2 * per_binder + 1)
    ]
    text = text_of(pdf_of(second))
    assert f"A-{per_binder + 1:05d}" in text and f"A-{2 * per_binder:05d}" in text
    assert "A-00001" not in text
    for kind in FormKind:
        assert first.sheets.filter(kind=kind).count() == service.SHEETS_PER_BINDER[kind]


def test_parity_22_each_form_and_page_is_marked(make_part: MakePart) -> None:
    make_part()
    binder = service.create_binder(actor=None)

    all_pages = pages(pdf_of(binder))
    stand = f"{binder.created_at.astimezone():%d.%m.%Y}"

    assert all(stand in page for page in all_pages)
    for sheet in binder.sheets.all():
        [page] = [page for page in all_pages if f"Formular {sheet.label}" in page]
        assert sheet.get_kind_display() in page
    loan_form = next(page for page in all_pages if "Formular A-" in page)
    for field in ("Entleiher", "Kurzkontrolle", "GAL bereitgestellt", "Rückgabe", "Sperrung"):
        assert field in loan_form
    inspection_form = next(page for page in all_pages if "Formular P-" in page)
    assert "nächste Prüfung" in inspection_form and "aussondern" in inspection_form


def test_parity_22_rules_on_first_page(make_part: MakePart) -> None:
    make_part()

    first = pages(pdf_of(service.create_binder(actor=None)))[0]

    assert "Notfallmappe" in first
    assert "nicht gesperrt" in first and "nach dem Rückgabedatum" in first
    assert "nur vorgemerkt" in first


def test_binder_file_matches_checksum(make_part: MakePart) -> None:
    make_part()

    binder = service.create_binder(actor=None)

    assert hashlib.sha256(pdf_of(binder)).hexdigest() == binder.sha256


def test_field_contents_are_set_as_text() -> None:
    create_product({"designation": '#panic("boom") ]*_ $x$', "ei": "TST-NF1"})

    assert '#panic("boom")' in text_of(pdf_of(service.create_binder(actor=None)))


def test_old_files_are_pruned_records_stay(make_part: MakePart) -> None:
    """Gespeicherte Mappen enthalten Namen; nur die letzten Fassungen bleiben als Datei."""
    make_part()

    binders = [service.create_binder(actor=None) for _ in range(service.KEEP_FILES + 2)]

    kept = [binder for binder in binders if service.path_of(binder).exists()]
    assert kept == binders[-service.KEEP_FILES :]
    assert Binder.objects.count() == len(binders)


def test_command_saves_a_binder(make_part: MakePart, capsys: pytest.CaptureFixture[str]) -> None:
    make_part()

    call_command("notfallmappe")

    binder = Binder.objects.get()
    assert binder.created_by is None and service.path_of(binder).exists()
    assert "A-00001" in capsys.readouterr().out


# --- Ansichten und Rechte ---


def test_parity_22_lender_creates_and_downloads(
    client: Client, lender: User, make_part: MakePart
) -> None:
    make_part()
    client.force_login(lender)

    created = client.post(reverse("emergency:create"))
    latest = client.get(reverse("emergency:latest"))
    page = client.get(reverse("emergency:index")).content.decode()

    assert created["Content-Type"] == "application/pdf"
    assert "attachment" in created["Content-Disposition"]
    binder = Binder.objects.get()
    assert binder.created_by == lender
    assert b"".join(created.streaming_content) == pdf_of(binder)  # type: ignore[attr-defined]
    assert b"".join(latest.streaming_content) == pdf_of(binder)  # type: ignore[attr-defined]
    assert "A-00001 bis A-00010" in page


def test_latest_without_binder(client: Client, lender: User) -> None:
    client.force_login(lender)

    response = client.get(reverse("emergency:latest"), follow=True)

    assert "Noch keine Notfallmappe" in response.content.decode()


def test_viewer_gets_no_binder(client: Client, make_part: MakePart) -> None:
    """Verbotener Fall: Die Mappe nennt Entleiher; Viewer sehen keine Personendaten."""
    make_part()
    service.create_binder(actor=None)
    client.force_login(make_inspector(role=Role.VIEWER, qualified=False))

    for response in (
        client.get(reverse("emergency:index")),
        client.get(reverse("emergency:latest")),
        client.post(reverse("emergency:create")),
    ):
        assert response.status_code == 403
    assert Binder.objects.count() == 1


def test_home_links_emergency_from_lender_up(client: Client, lender: User) -> None:
    client.force_login(lender)
    assert reverse("emergency:index") in client.get(reverse("home")).content.decode()

    client.force_login(make_inspector(role=Role.VIEWER, qualified=False))
    assert reverse("emergency:index") not in client.get(reverse("home")).content.decode()
