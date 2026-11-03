"""Mitgliedsausweis als Ausleihausweis (#125).

Festlegung (Felix, 31.10.2026): DAV-Mitgliedsausweis mit Strichcode; gespeichert wird die
Mitgliedsnummer (11 Ziffern) lokal am Entleiher, eindeutig, und mit den Kontaktdaten nach der
Löschfrist gelöscht. Verbinden darf der Verleiher (nach Abgleich des Namens), lösen oder ändern
erst Trainer/Materialwart. Personen legt man auch ohne Ausleihe an. Das Protokoll nennt die
Nummer nicht.
"""

import pytest
from django.core.exceptions import PermissionDenied
from django.test import Client
from django.urls import reverse

from accounts.models import Role, User
from core import chain
from lending import members
from lending.forms import BorrowerForm
from lending.models import Borrower, BorrowerContact, MemberCardEvent
from lending.services import LendingError, create_borrower, erase_personal_data
from tests.helpers import make_inspector

pytestmark = pytest.mark.django_db
NUMBER = "99900004711"
# Ganze Zeile des Strichcodes im Aufbau des DAV-Musters, künstlich: Sektion/Ortsgruppe/Mitglied,
# Kategorie, Eintritt DAV, Eintritt Sektion, Geburtsdatum.
BIRTH = "01011990"
CARD_LINE = f"999/00/004711*1000*2020*2020*{BIRTH}"


@pytest.fixture
def lender(db: None) -> User:
    return make_inspector(role=Role.LENDER, qualified=False)


@pytest.fixture
def borrower(lender: User) -> Borrower:
    return create_borrower("Kim Beispiel", address="Teststraße 1", actor=lender)


def number_of(borrower: Borrower) -> str:
    return BorrowerContact.objects.get(borrower=borrower).member_number


# --- Nummer ---


@pytest.mark.parametrize(
    "raw",
    [
        "99900004711",
        "999 00 004711",
        "999/00/004711",
        " 999-00-004711 ",
        CARD_LINE,
        f"{CARD_LINE}\r\n",
        "999/00/004711 * 1000 * 2020",
        "999/00/004711*",
    ],
)
def test_number_is_normalized(raw: str) -> None:
    """Ausdruck, Eingabe oder ganze Zeile des Strichcodes: übrig bleiben nur die 11 Ziffern."""
    assert members.normalize(raw) == NUMBER


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "9990000471",
        "999000047111",
        "99900A04711",
        "9990000471110002020202001011990",  # ohne Trenner: Nummer nicht eindeutig abgrenzbar
        "1000*2020*2020*01011990",
        "*999/00/004711",
    ],
)
def test_malformed_number_is_rejected(raw: str) -> None:
    with pytest.raises(LendingError, match="11 Ziffern"):
        members.normalize(raw)


def test_card_line_keeps_nothing_but_the_number(lender: User) -> None:
    """Geburtsdatum, Kategorie, Eintrittsjahre: nirgends gespeichert (Felix, 03.11.2026)."""
    person = create_borrower("Mara Muster", member_number=CARD_LINE, actor=lender)

    contact = BorrowerContact.objects.get(borrower=person)
    assert contact.member_number == NUMBER
    assert contact.member_number_display == "999/00/004711"
    stored = str(BorrowerContact.objects.values().get()) + str(
        MemberCardEvent.objects.values().get()
    )
    assert BIRTH not in stored and "*" not in stored


# --- Verbinden, lösen, ändern ---


def test_lender_links_card_and_scan_finds_the_person(borrower: Borrower, lender: User) -> None:
    members.link(borrower, "999 00 004711", actor=lender)

    assert number_of(borrower) == NUMBER
    assert members.find(NUMBER, actor=lender) == borrower
    event = MemberCardEvent.objects.get()
    assert (event.borrower, event.kind, event.actor) == (borrower, "linked", lender)
    assert NUMBER not in str(MemberCardEvent.objects.values().get())
    assert chain.verify() == []


def test_unknown_number_finds_nobody(lender: User) -> None:
    assert members.find("99900008150", actor=lender) is None


def test_same_number_for_two_people_is_rejected(borrower: Borrower, lender: User) -> None:
    members.link(borrower, NUMBER, actor=lender)
    other = create_borrower("Noa Probe", actor=lender)

    with pytest.raises(LendingError, match="anderen Person"):
        members.link(other, NUMBER, actor=lender)


def test_lender_cannot_replace_a_linked_card(borrower: Borrower, lender: User) -> None:
    members.link(borrower, NUMBER, actor=lender)

    with pytest.raises(LendingError, match="Trainer/Materialwart"):
        members.link(borrower, "99900008150", actor=lender)
    with pytest.raises(PermissionDenied):
        members.change(borrower, "99900008150", actor=lender)
    with pytest.raises(PermissionDenied):
        members.change(borrower, "", actor=lender)


def test_trainer_changes_and_unlinks(borrower: Borrower, lender: User) -> None:
    trainer = make_inspector(role=Role.TRAINER, qualified=False)
    members.link(borrower, NUMBER, actor=lender)

    members.change(borrower, "99900008150", actor=trainer)
    assert number_of(borrower) == "99900008150"
    members.change(borrower, "", actor=trainer)
    assert number_of(borrower) == ""
    kinds = list(MemberCardEvent.objects.values_list("kind", flat=True))
    assert kinds == ["linked", "changed", "unlinked"]


def test_viewer_may_not_link(borrower: Borrower) -> None:
    with pytest.raises(PermissionDenied):
        members.link(borrower, NUMBER, actor=make_inspector(role=Role.VIEWER, qualified=False))


def test_new_person_with_card(lender: User) -> None:
    person = create_borrower("Mara Muster", member_number="999 00 008150", actor=lender)

    assert number_of(person) == "99900008150"
    assert MemberCardEvent.objects.get().kind == "linked"


def test_new_person_with_taken_card_is_rejected(borrower: Borrower, lender: User) -> None:
    members.link(borrower, NUMBER, actor=lender)

    with pytest.raises(LendingError, match="anderen Person"):
        create_borrower("Mara Muster", member_number=NUMBER, actor=lender)
    assert not Borrower.objects.filter(contact__name="Mara Muster").exists()


def test_erasure_removes_the_number(borrower: Borrower, lender: User) -> None:
    members.link(borrower, NUMBER, actor=lender)

    erase_personal_data(borrower, actor=None)

    assert members.find(NUMBER, actor=lender) is None
    assert MemberCardEvent.objects.filter(borrower=borrower).exists()


# --- Oberfläche ---


def test_search_by_scanned_number(client: Client, borrower: Borrower, lender: User) -> None:
    members.link(borrower, NUMBER, actor=lender)
    client.force_login(lender)

    page = client.get(reverse("lending:borrower_search"), {"suche": "999 00 004711"})

    assert page.context["found"] == [borrower]


def test_search_by_whole_card_line(client: Client, borrower: Borrower, lender: User) -> None:
    """Kommt doch die ganze Zeile an (Handscanner ohne Skript), findet die Suche die Person."""
    members.link(borrower, NUMBER, actor=lender)
    client.force_login(lender)

    page = client.get(reverse("lending:borrower_search"), {"suche": CARD_LINE})

    assert page.context["found"] == [borrower]
    assert BIRTH not in page.content.decode()


def test_person_page_creates_without_loan(client: Client, lender: User) -> None:
    client.force_login(lender)

    response = client.post(
        reverse("lending:borrower_create"),
        {
            "name": "Mara Muster",
            "address": "Teststraße 2, 00000 Beispielstadt",
            "phone": "0000 000000",
            "email": "",
            "member_number": "99900008150",
        },
    )

    assert response.status_code == 302
    person = Borrower.objects.get(contact__name="Mara Muster")
    assert number_of(person) == "99900008150"


def test_card_page_links_after_name_check(client: Client, borrower: Borrower, lender: User) -> None:
    client.force_login(lender)
    url = reverse("lending:member_card", args=[borrower.pk])

    refused = client.post(url, {"member_number": NUMBER})
    assert number_of(borrower) == ""
    assert refused.status_code == 200

    response = client.post(url, {"member_number": NUMBER, "name_checked": "on"})
    assert response.status_code == 302 and number_of(borrower) == NUMBER


def test_card_page_hides_the_number_from_lenders(
    client: Client, borrower: Borrower, lender: User
) -> None:
    members.link(borrower, NUMBER, actor=lender)
    client.force_login(lender)

    page = client.get(reverse("lending:member_card", args=[borrower.pk])).content.decode()

    assert "Mitgliedsausweis verbunden" in page and NUMBER not in page


def test_viewer_has_no_card_page(client: Client, borrower: Borrower) -> None:
    client.force_login(make_inspector(role=Role.VIEWER, qualified=False))

    assert client.get(reverse("lending:member_card", args=[borrower.pk])).status_code == 403


def test_unlinked_card_in_search_explains_what_to_do(client: Client, lender: User) -> None:
    client.force_login(lender)

    page = client.get(reverse("lending:borrower_search"), {"suche": NUMBER})

    assert page.context["found"] == []
    assert "noch mit niemandem verbunden" in page.content.decode()


def test_checkout_form_links_no_card_to_a_known_person(borrower: Borrower) -> None:
    """Bei bekannter Person nur über „Mitgliedsausweis verbinden“ (Namensabgleich)."""
    form = BorrowerForm({"borrower": borrower.pk, "member_number": NUMBER})

    assert not form.is_valid()
    assert "Mitgliedsausweis verbinden" in str(form.non_field_errors())


def test_lender_cannot_unlink_on_the_card_page(
    client: Client, borrower: Borrower, lender: User
) -> None:
    members.link(borrower, NUMBER, actor=lender)
    client.force_login(lender)
    url = reverse("lending:member_card", args=[borrower.pk])

    page = client.post(url, {"aktion": "aendern", "member_number": ""})

    assert page.status_code == 200 and number_of(borrower) == NUMBER
    assert "Ändern oder lösen" not in client.get(url).content.decode()


@pytest.mark.parametrize(
    ("name", "args"),
    [("lending:borrower_create", []), ("lending:member_card", None), ("lending:new_booking", [])],
)
def test_pages_offer_the_camera(
    client: Client, borrower: Borrower, lender: User, name: str, args: list[int] | None
) -> None:
    client.force_login(lender)
    url = reverse(name, args=[borrower.pk] if args is None else args)

    page = client.get(url).content.decode()

    assert "data-card-scan" in page and "js/ausweis.js" in page


def test_person_page_takes_the_whole_card_line(client: Client, lender: User) -> None:
    """Ohne Skript (Handscanner) kommt die ganze Zeile an; der Server behält nur die Nummer."""
    client.force_login(lender)

    response = client.post(
        reverse("lending:borrower_create"),
        {
            "name": "Mara Muster",
            "address": "",
            "phone": "",
            "email": "",
            "member_number": CARD_LINE,
        },
    )

    assert response.status_code == 302
    person = Borrower.objects.get(contact__name="Mara Muster")
    assert number_of(person) == NUMBER
