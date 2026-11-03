"""Schadensgründe an der Theke (#127, Teil 2).

Festlegung (Felix, 03.11.2026): Gründe wie im Prototyp. Der Grund steht als Text im
Sperrkommentar, z. B. „Sturz belastet: harter Sturz am Seil“; ein eigenes Feld gibt es nicht.
Bei der Rückgabe mit Schaden reicht der Grund, ein Kommentar ist freiwillig; bei „Sonstiges“
ist er Pflicht. „Schaden melden“ verlangt immer Grund und Kommentar.
"""

OTHER = "Sonstiges"
REASONS = (
    "Sichtbarer Schaden",
    "Sturz belastet",
    "Chemikalien / Hitze",
    "Unvollständig",
    "Abgelaufen",
    OTHER,
)


class DamageError(ValueError):
    """Grund oder Kommentar fehlt; der Text sagt, was."""


def lock_comment(reason: str, comment: str, *, comment_required: bool) -> str:
    """Sperrkommentar aus Grund und Kommentar; prüft die Pflichtangaben."""
    if reason not in REASONS:
        raise DamageError("Bitte einen Grund antippen.")
    comment = comment.strip()
    if not comment and (comment_required or reason == OTHER):
        raise DamageError("Bitte kurz beschreiben, was beschädigt ist.")
    return f"{reason}: {comment}" if comment else reason
