"""Rechtetabelle (Konzept, Abschnitt 5): jede Zeile, jede Rolle, mit und ohne Sachkunde (#12).

`EXPECTED` ist die Konzepttabelle, hier unabhängig vom Code abgeschrieben. Kürzel je Rolle
(Admin, PSA-Sachkundig, Trainer/Materialwart, Verleiher, Viewer):

- `+` erlaubt
- `q` erlaubt, nur mit gültiger Sachkunde
- `o` nur eingeschränkt („nur eigene“, „nur laufende Ausleihen“)
- `oq` eingeschränkt und nur mit gültiger Sachkunde
- `-` verboten
"""

from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied
from django.utils import timezone

from accounts.models import ROLE_ORDER, Qualification, Role, User
from accounts.rights import RIGHTS, Right, denial, has_right, may_use, require, table_markdown
from inventory.services import create_product, link_document
from lending.services import create_borrower, lend, reserve, return_loans
from tests.helpers import adopt_inspection, make_inspector

pytestmark = pytest.mark.django_db

ROLES = (Role.ADMIN, Role.PPE_EXPERT, Role.TRAINER, Role.LENDER, Role.VIEWER)
EXPECTED: dict[Right, str] = {
    Right.ACCOUNT: "+ + + + +",
    Right.VIEW_AVAILABILITY: "+ + + + +",
    Right.VIEW_CONTACTS: "+ + o o -",
    Right.LEND: "+ + + + -",
    Right.CHANGE_OTHERS_RESERVATIONS: "+ + + o -",
    Right.LOCK: "+ + + + -",
    Right.BACKFILL_LOANS: "+ + o o -",
    Right.BACKFILL_INSPECTIONS: "q oq - - -",
    Right.MANAGE_PARTS: "+ + + - -",
    Right.PPE_DOCUMENTS: "q q - - -",
    Right.PPE_FIELDS: "q q - - -",
    Right.EXPORT_PERSONAL: "+ + - - -",
    Right.INSPECT: "q q - - -",
    Right.MANAGE_USERS: "+ - - - -",
    Right.MODIFY_SIGNED: "- - - - -",
}
QUALIFICATIONS = ("gültig", "abgelaufen", "keine")
LENT_BY_COMMAND: dict[str, Any] = {
    "actor": None,
    "quick_check": True,
    "gal_provided": True,
    "signature": [[[1, 1], [2, 2]]],
    "signer_name": "Kim Beispiel",
}
DOCS = Path(__file__).resolve().parents[2] / "docs" / "rechte.md"


def code(right: Right, role: Role) -> str:
    return EXPECTED[right].split()[ROLES.index(role)]


def person(role: Role, qualification: str) -> User:
    user = make_inspector(role=role, qualified=qualification == "gültig")
    if qualification == "abgelaufen":
        today = timezone.localdate()
        Qualification.objects.create(
            user=user,
            valid_from=today - timedelta(days=800),
            valid_until=today - timedelta(days=1),
            evidence="abgelaufen (Testdaten)",
        )
    return user


def objects(right: Right, user: User) -> tuple[object, object]:
    """Ein Objekt, das unter die Einschränkung fällt, und eines, das nicht darunter fällt."""
    if right == Right.VIEW_CONTACTS:
        part = create_product({"designation": "Klettergurt", "ei": f"TST-R{user.pk}"})
        part.documents.add(link_document("https://example.org/gal.pdf", title="GAL"))
        adopt_inspection([part], next_due=timezone.localdate() + timedelta(days=365))
        active = create_borrower("Kim Beispiel", address="Teststraße 1", email="kim@example.org")
        lend(active, [part], timezone.localdate(), timezone.localdate(), **LENT_BY_COMMAND)
        return active, create_borrower("Noa Probe")
    return SimpleNamespace(created_by_id=user.pk), SimpleNamespace(created_by_id=-1)


def test_every_concept_row_is_in_the_code() -> None:
    assert set(EXPECTED) == set(Right) == set(RIGHTS)


@pytest.mark.parametrize("qualification", QUALIFICATIONS)
@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("right", list(Right))
def test_rights_matrix(right: Right, role: Role, qualification: str) -> None:
    """Erlaubter und verbotener Fall je Zeile und Rolle; Sachkunde gültig, abgelaufen, keine."""
    user = person(role, qualification)
    inside, outside = objects(right, user)
    entry = code(right, role)
    qualified = qualification == "gültig"
    allowed = entry != "-" and (qualified or "q" not in entry)
    restricted = "o" in entry

    assert may_use(user, right) is allowed
    assert has_right(user, right, inside) is allowed
    assert has_right(user, right, outside) is (allowed and not restricted)
    assert has_right(user, right) is (allowed and not restricted)
    if allowed and not restricted:
        require(user, right, outside)
    else:
        with pytest.raises(PermissionDenied):
            require(user, right, outside)


@pytest.mark.parametrize("right", list(Right))
def test_inactive_account_has_no_rights(right: Right) -> None:
    user = make_inspector(role=Role.ADMIN, is_active=False)

    assert not may_use(user, right)
    with pytest.raises(PermissionDenied):
        require(user, right, SimpleNamespace(created_by_id=user.pk))


@pytest.mark.parametrize("right", list(Right))
def test_anonymous_has_no_rights(right: Right) -> None:
    assert not may_use(AnonymousUser(), right)
    assert not has_right(AnonymousUser(), right)


def test_qualification_counts_at_the_given_time() -> None:
    user = make_inspector()
    later = timezone.now() + timedelta(days=400)

    assert has_right(user, Right.INSPECT)
    assert not has_right(user, Right.INSPECT, at=later)
    with pytest.raises(PermissionDenied):
        require(user, Right.INSPECT, at=later)


def test_actor_none_is_import_or_command() -> None:
    """Übernahme und Verwaltungsbefehle laufen ohne Konto; Ansichten übergeben immer eines."""
    require(None, Right.INSPECT)


def test_reservation_does_not_reveal_contacts() -> None:
    """Befund der Sicherheitsprüfung: Eine eigene Reservierung schaltet keine Adresse frei."""
    lender = make_inspector(role=Role.LENDER, qualified=False)
    part = create_product({"designation": "Klettergurt", "ei": "TST-RES"})
    adopt_inspection([part], next_due=timezone.localdate() + timedelta(days=365))
    borrower = create_borrower("Kim Beispiel")

    booking = reserve(borrower, [part], timezone.localdate(), timezone.localdate(), actor=lender)

    assert not has_right(lender, Right.VIEW_CONTACTS, borrower)
    assert not has_right(lender, Right.VIEW_CONTACTS, booking)
    assert not has_right(lender, Right.VIEW_CONTACTS, booking.loans.get())


def test_returned_loan_no_longer_reveals_contacts() -> None:
    lender = make_inspector(role=Role.LENDER, qualified=False)
    part = create_product({"designation": "Klettergurt", "ei": "TST-RET"})
    part.documents.add(link_document("https://example.org/gal/ret.pdf", title="GAL"))
    adopt_inspection([part], next_due=timezone.localdate() + timedelta(days=365))
    borrower = create_borrower("Kim Beispiel", address="Teststraße 1", email="kim@example.org")
    booking = lend(borrower, [part], timezone.localdate(), timezone.localdate(), **LENT_BY_COMMAND)
    assert has_right(lender, Right.VIEW_CONTACTS, borrower)

    return_loans(
        list(booking.loans.all()),
        actor=None,
        quick_check=True,
        signature=[[[1, 1], [2, 2]]],
        signer_name="Kim Beispiel",
    )

    assert not has_right(lender, Right.VIEW_CONTACTS, borrower)


def test_lent_loan_reveals_contacts_to_lender() -> None:
    lender = make_inspector(role=Role.LENDER, qualified=False)
    part = create_product({"designation": "Klettergurt", "ei": "TST-AUS"})
    part.documents.add(link_document("https://example.org/gal/aus.pdf", title="GAL Test"))
    adopt_inspection([part], next_due=timezone.localdate() + timedelta(days=365))
    borrower = create_borrower(
        "Kim Beispiel", address="Teststraße 1, 00000 Beispielstadt", email="kim@example.org"
    )
    booking = lend(
        borrower,
        [part],
        timezone.localdate(),
        timezone.localdate(),
        actor=lender,
        quick_check=True,
        gal_provided=True,
        signature=[[[1, 1], [2, 2]]],
        signer_name="Kim Beispiel",
    )

    assert has_right(lender, Right.VIEW_CONTACTS, borrower)
    assert has_right(lender, Right.VIEW_CONTACTS, booking.loans.get())


def test_unrelated_object_is_not_own() -> None:
    lender = make_inspector(role=Role.LENDER, qualified=False)

    assert not has_right(lender, Right.CHANGE_OTHERS_RESERVATIONS, object())
    assert not has_right(lender, Right.VIEW_CONTACTS, object())


@pytest.mark.parametrize("right", list(Right))
def test_denial_names_role_and_qualification(right: Right) -> None:
    rule = RIGHTS[right]
    text = denial(right)

    assert rule.label in text
    if rule.minimum is not None:
        assert rule.minimum.label in text
    assert ("nur mit gültiger Sachkunde" in text) is rule.qualification


def test_roles_cover_role_order() -> None:
    assert tuple(reversed(ROLE_ORDER)) == ROLES


def test_docs_show_the_table_from_the_code() -> None:
    """`docs/rechte.md` gibt die Tabelle aus `accounts/rights.py` wieder."""
    assert table_markdown() in DOCS.read_text(encoding="utf-8")
