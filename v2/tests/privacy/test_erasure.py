"""Entleiher löschen (#63): Name, Kontaktdaten und Unterschrift verschwinden, die Historie bleibt.

Die Unterschrift steht nicht in der Tabelle, die nur angehängt wird, sondern daneben in einer
löschbaren Tabelle. Der Vorgang behält eine gesalzene SHA-256-Prüfsumme; sie belegt weiter, dass
nichts verändert wurde, ohne dass sich Name oder Unterschrift daraus zurückgewinnen lassen.
"""

from typing import Any

import pytest
from django.core.exceptions import PermissionDenied
from django.db import connection
from django.test import Client
from django.urls import reverse

from accounts.models import Role, User
from lending.models import (
    Borrower,
    BorrowerContact,
    Loan,
    LoanEvent,
    Signature,
    SignatureContent,
    signature_digest,
)
from lending.services import create_borrower, lend, return_loans
from privacy.services import erase_borrower
from tests.helpers import make_inspector
from tests.lending.conftest import DAY, TODAY, MakePart, checkout_kwargs, return_kwargs

pytestmark = pytest.mark.django_db

# Unverwechselbare Werte, damit die Suche in der ganzen Datenbank eindeutig ist.
NAME = "Quirin Löschprobe"
SIGNER = "Quirina Unterschriftprobe"
EMAIL = "quirin.loeschprobe@example.org"
PHONE = "0000 4711 0815"
ADDRESS = "Löschgasse 17, 00000 Beispielstadt"
STROKES = [[[1111.5, 2222.25], [3333.75, 4444.125]], [[5555.5, 6666.25]]]


def database_text() -> str:
    """Jede Zeile jeder Tabelle als Text: Was hier nicht steht, steht nirgends in der Datenbank."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        tables = [row[0] for row in cursor.fetchall()]
        chunks: list[str] = []
        for table in tables:
            cursor.execute(f"SELECT t::text FROM {connection.ops.quote_name(table)} t")  # noqa: S608 – Tabellennamen aus pg_tables
            chunks.extend(row[0] for row in cursor.fetchall())
    return "\n".join(chunks)


def personal_data_in_database() -> list[str]:
    text = database_text()
    return [value for value in (NAME, SIGNER, EMAIL, PHONE, ADDRESS, "2222.25") if value in text]


def signed(lender: User, make_part: MakePart, borrower: Borrower) -> Loan:
    kwargs: dict[str, Any] = {
        **checkout_kwargs(lender),
        "signature": STROKES,
        "signer_name": SIGNER,
    }
    booking = lend(borrower, [make_part()], TODAY, TODAY + DAY, **kwargs)
    loans = list(booking.loans.all())
    return_loans(loans, **{**return_kwargs(lender), "signature": STROKES, "signer_name": SIGNER})
    return loans[0]


def signatures(loan: Loan) -> list[Signature]:
    """Unterschriften bei Ausgabe und Rückgabe."""
    found = [event.signature for event in loan.events.all()]
    assert all(found)
    return [signature for signature in found if signature is not None]


@pytest.fixture
def person(db: None) -> Borrower:
    return create_borrower(NAME, address=ADDRESS, email=EMAIL, phone=PHONE)


@pytest.fixture
def admin(db: None) -> User:
    return make_inspector(role=Role.ADMIN, qualified=False)


# --- Unterschrift ausgelagert ---


def test_signature_row_holds_only_the_digest(
    lender: User, make_part: MakePart, person: Borrower
) -> None:
    loan = signed(lender, make_part, person)

    signature = signatures(loan)[0]
    assert {field.attname for field in Signature._meta.concrete_fields} == {
        "id",
        "content_sha256",
        "origin",
        "created_at",
    }
    content = signature.content
    assert (content.signer_name, content.strokes) == (SIGNER, STROKES)
    assert signature.content_sha256 == signature_digest(content.salt, SIGNER, STROKES)
    assert signature.verify() is True


def test_digest_is_salted(lender: User, make_part: MakePart, person: Borrower) -> None:
    """Gleicher Name und gleiche Striche geben verschiedene Prüfsummen: kein Zurückrechnen."""
    loan = signed(lender, make_part, person)

    checked_out, returned = signatures(loan)
    assert checked_out.content_sha256 != returned.content_sha256
    assert len(checked_out.content.salt) == 32


@pytest.mark.parametrize(
    "change", [{"signer_name": "Jemand Anderes"}, {"strokes": [[[1, 1], [2, 2]]]}]
)
def test_digest_detects_changed_content(
    change: dict[str, Any], lender: User, make_part: MakePart, person: Borrower
) -> None:
    signature = signatures(signed(lender, make_part, person))[0]
    SignatureContent.objects.filter(signature=signature).update(**change)

    signature = Signature.objects.get(pk=signature.pk)
    assert signature.verify() is False


def test_signature_stays_append_only(lender: User, make_part: MakePart, person: Borrower) -> None:
    signature = signatures(signed(lender, make_part, person))[0]

    with pytest.raises(Exception, match="nur zum Anhängen"):
        Signature.objects.filter(pk=signature.pk).update(content_sha256="0" * 64)


# --- Löschen ---


def test_erasing_removes_every_trace_of_the_person(
    lender: User, make_part: MakePart, person: Borrower, admin: User
) -> None:
    signed(lender, make_part, person)
    assert set(personal_data_in_database()) == {NAME, SIGNER, EMAIL, PHONE, ADDRESS, "2222.25"}

    erase_borrower(person, actor=admin)

    assert personal_data_in_database() == []


def test_erasing_keeps_the_history(
    lender: User, make_part: MakePart, person: Borrower, admin: User
) -> None:
    loan = signed(lender, make_part, person)
    events = list(LoanEvent.objects.filter(loan=loan).values_list("pk", "kind", "signature_id"))
    digests = {s.pk: s.content_sha256 for s in Signature.objects.all()}
    copies = {
        c.signature_id: (c.salt, c.signer_name, c.strokes) for c in SignatureContent.objects.all()
    }

    erase_borrower(person, actor=admin)

    assert (
        list(LoanEvent.objects.filter(loan=loan).values_list("pk", "kind", "signature_id"))
        == events
    )
    assert {s.pk: s.content_sha256 for s in Signature.objects.all()} == digests
    assert not SignatureContent.objects.exists()
    # Wer eine Kopie hat (etwa der Entleiher selbst), kann sie weiter gegen die Prüfsumme halten.
    for pk, copy in copies.items():
        assert signature_digest(*copy) == digests[pk]
    assert Signature.objects.get(pk=events[0][2]).verify() is None


def test_erasing_touches_only_this_borrower(
    lender: User, make_part: MakePart, person: Borrower, borrower: Borrower, admin: User
) -> None:
    signed(lender, make_part, person)
    lend(borrower, [make_part()], TODAY, TODAY + DAY, **checkout_kwargs(lender))

    erase_borrower(person, actor=admin)

    assert BorrowerContact.objects.filter(borrower=borrower).exists()
    [kept] = SignatureContent.objects.all()
    assert kept.signer_name == "Kim Beispiel"


def test_erased_borrower_shows_placeholders(
    client: Client, lender: User, make_part: MakePart, person: Borrower, admin: User
) -> None:
    loan = signed(lender, make_part, person)
    erase_borrower(person, actor=admin)
    client.force_login(lender)

    page = client.get(reverse("lending:booking_detail", args=[loan.booking_id])).content.decode()

    assert NAME not in page and SIGNER not in page
    assert "Kontaktdaten gelöscht" in page
    assert "Name und Unterschrift nach Ablauf der Löschfrist gelöscht" in page


# --- Rechte ---


@pytest.mark.parametrize("role", [Role.PPE_EXPERT, Role.TRAINER, Role.LENDER, Role.VIEWER])
def test_only_admin_erases_borrowers(
    role: Role, lender: User, make_part: MakePart, person: Borrower
) -> None:
    """Verbotener Fall: Nur Admin (oder ein Befehl) löscht; dabei ändert sich nichts."""
    signed(lender, make_part, person)

    with pytest.raises(PermissionDenied, match="ab der Rolle Admin"):
        erase_borrower(person, actor=make_inspector(role=role, qualified=False))

    assert BorrowerContact.objects.filter(borrower=person).exists()
    assert SignatureContent.objects.count() == 2


def test_command_may_erase(lender: User, make_part: MakePart, person: Borrower) -> None:
    signed(lender, make_part, person)

    erase_borrower(person, actor=None)

    assert personal_data_in_database() == []
