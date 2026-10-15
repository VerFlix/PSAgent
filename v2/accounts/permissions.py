"""Rechteprüfungen, die mehrere Apps brauchen. Die vollständige Rechtetabelle folgt mit #12.

`actor=None` steht für Vorgänge ohne angemeldete Person, also die Übernahme aus dem Altsystem
und Verwaltungsbefehle. Ansichten übergeben immer die angemeldete Person.
"""

from datetime import datetime

from django.core.exceptions import PermissionDenied

from accounts.models import User


def require_inspection_right(actor: User | None, action: str, at: datetime | None = None) -> None:
    """Weist `action` ab, wenn `actor` zum Zeitpunkt `at` (Standard: jetzt) nicht prüfen darf.

    Prüfen darf, wer mindestens die Rolle PSA-Sachkundig und eine gültige Sachkunde hat
    (Konzept, Abschnitt 5). Das gilt auch für Entsperren, Aussondern, „keine PSA“ setzen und
    PSA-relevante Felder ändern.
    """
    if actor is not None and not actor.may_inspect(at):
        raise PermissionDenied(
            f"{action} ist nur mit gültiger Sachkunde und der Rolle PSA-Sachkundig erlaubt."
        )
