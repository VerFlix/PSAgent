"""Mitgliedsausweis als Ausleihausweis (#125).

Festlegung (Felix, 31.10.2026): Der DAV-Mitgliedsausweis trägt einen Strichcode; gespeichert
wird die Mitgliedsnummer (11 Ziffern, Sektionsnummer zuerst) nur lokal in den Kontaktdaten des
Entleihers. Sie ist eindeutig und verschwindet mit den Kontaktdaten nach der Löschfrist (#63).
Verbinden darf der Verleiher nach Abgleich des Namens auf dem Ausweis; lösen oder ändern erst
Trainer/Materialwart. Das Protokoll (`MemberCardEvent`) nennt die Nummer nicht.

Aufbau des Strichcodes (Felix, 03.11.2026, nach dem DAV-Muster): „SSS/OO/MMMMMM*KKKK*JJJJ*JJJJ*
TTMMJJJJ“, also Sektion, Ortsgruppe und Mitgliedsnummer, dann Kategorie, Eintrittsjahr DAV und
Sektion und das Geburtsdatum. Gespeichert werden nur die ersten 11 Ziffern (DAV-weit eindeutig).
Alles ab dem ersten „*“ wird verworfen: im Browser, bevor es an den Server geht, und hier noch
einmal, falls es doch ankommt (Handscanner, Eintippen).
"""

import re

from django.db import IntegrityError, transaction

from accounts.models import User
from accounts.rights import Right, require
from lending.models import Borrower, BorrowerContact, MemberCardEvent, MemberCardEventKind
from lending.services import LendingError

DIGITS = 11
TAKEN = "Dieser Mitgliedsausweis ist schon mit einer anderen Person verbunden."
WRONG_FORMAT = (
    f"Die Mitgliedsnummer hat {DIGITS} Ziffern: Sektion/Ortsgruppe/Mitglied, vorne in der Zeile "
    "unter dem Strichcode, z. B. 123/00/456789."
)
# Sektion (3), Ortsgruppe (2), Mitglied (6), getrennt durch nichts, „/“, „.“, „-“ oder Leerzeichen;
# danach höchstens der Rest des Strichcodes ab dem ersten „*“.
CARD = re.compile(r"\s*(\d{3})\s*[/.\-]?\s*(\d{2})\s*[/.\-]?\s*(\d{6})\s*(?:\*.*)?", re.DOTALL)


def normalize(raw: str) -> str:
    """Die 11 Ziffern aus Ausdruck oder Strichcode; der Rest (z. B. Geburtsdatum) fällt weg."""
    match = CARD.fullmatch(raw or "")
    if match is None:
        raise LendingError(WRONG_FORMAT)
    return "".join(match.groups())


def find(raw: str, *, actor: User | None) -> Borrower | None:
    """Der Entleiher zu einem gescannten oder eingetippten Ausweis, sonst None."""
    require(actor, Right.LEND)
    contact = (
        BorrowerContact.objects.select_related("borrower")
        .filter(member_number=normalize(raw))
        .first()
    )
    return contact.borrower if contact else None


def _store(contact: BorrowerContact, number: str) -> None:
    contact.member_number = number
    try:
        with transaction.atomic():
            contact.save(update_fields=["member_number"])
    except IntegrityError as error:
        raise LendingError(TAKEN) from error


@transaction.atomic
def link(borrower: Borrower, raw: str, *, actor: User | None) -> None:
    """Verbindet den Ausweis mit einem Entleiher, der noch keinen hat (ab Verleiher)."""
    require(actor, Right.LINK_MEMBER_CARD)
    number = normalize(raw)
    contact = BorrowerContact.objects.select_for_update().get(borrower=borrower)
    if contact.member_number == number:
        return
    if contact.member_number:
        raise LendingError(
            "Diese Person ist schon mit einem anderen Mitgliedsausweis verbunden; ändern können "
            "das nur Trainer/Materialwart."
        )
    if BorrowerContact.objects.filter(member_number=number).exclude(pk=contact.pk).exists():
        raise LendingError(TAKEN)
    _store(contact, number)
    MemberCardEvent.objects.create(borrower=borrower, kind=MemberCardEventKind.LINKED, actor=actor)


@transaction.atomic
def change(borrower: Borrower, raw: str, *, actor: User | None) -> None:
    """Ändert die Verbindung oder löst sie (leer); erst ab Trainer/Materialwart."""
    require(actor, Right.CHANGE_MEMBER_CARD)
    contact = BorrowerContact.objects.select_for_update().get(borrower=borrower)
    if not raw.strip():
        if contact.member_number:
            _store(contact, "")
            MemberCardEvent.objects.create(
                borrower=borrower, kind=MemberCardEventKind.UNLINKED, actor=actor
            )
        return
    number = normalize(raw)
    if number == contact.member_number:
        return
    if BorrowerContact.objects.filter(member_number=number).exclude(pk=contact.pk).exists():
        raise LendingError(TAKEN)
    kind = MemberCardEventKind.CHANGED if contact.member_number else MemberCardEventKind.LINKED
    _store(contact, number)
    MemberCardEvent.objects.create(borrower=borrower, kind=kind, actor=actor)
