"""Personendaten eines Entleihers löschen (#63, Konzept Abschnitte 4 und 7).

Gelöscht werden Kontaktdaten, Name und Strichliste aller Unterschriften zu seinen Ausleihen
und die Scans seiner Ausleihformulare aus dem Notbetrieb. Die Ausleihhistorie bleibt mit den
Prüfsummen vollständig; der Entleiher erscheint danach als „Entleiher #<Nummer>“.

Freitext (Kommentare an Vorgängen) bleibt, weil er PSA-relevant sein kann.
"""

from django.db import transaction

from accounts.models import User
from accounts.rights import Right, require
from emergency.backfill import erase_scans
from lending.models import Borrower
from lending.services import erase_personal_data


def erase_borrower(borrower: Borrower, *, actor: User | None) -> None:
    """Löscht alle Personendaten des Entleihers. Nur Admin; `None` = Verwaltungsbefehl."""
    require(actor, Right.MANAGE_USERS)
    with transaction.atomic():
        erase_personal_data(borrower, actor=actor)
        erase_scans(borrower, actor=actor)
