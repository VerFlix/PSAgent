"""Der laufende Ausgabe-Vorgang an der Theke, in der Sitzung (#127).

Gespeichert sind nur Verweise (Teil- und Entleiher-Nummern) und das Rückgabedatum, keine
Personendaten. Neue Personen und neue Kontaktdaten gehen sofort über die Services in die
Datenbank. Die Sitzung endet am Vereinsgerät nach dem Vorgang und nach 3 Minuten Pause; damit
verschwindet auch der Vorgang.
"""

from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any

from django.http import HttpRequest

SESSION_KEY = "psagent_theke_ausgabe"


@dataclass
class Line:
    part: int
    kind: str = ""  # Art-Kürzel, wenn über den Schnellzugriff gewählt; sonst „von Hand“
    wanted: str | None = None  # gewünschte Gurtgröße; "" = ohne Größenangabe


@dataclass
class Cart:
    end: date | None = None
    lines: list[Line] = field(default_factory=list)
    borrower: int | None = None
    confirmed: bool = False  # Kontaktdaten nachgefragt („seit … unverändert“) oder neu
    reservation: int | None = None  # Vorgang mit Reservierungen, die abgeholt werden

    def part_ids(self) -> list[int]:
        return [line.part for line in self.lines]

    def to_session(self) -> dict[str, Any]:
        data = asdict(self)
        data["end"] = self.end.isoformat() if self.end else None
        return data

    @classmethod
    def from_session(cls, data: dict[str, Any] | None) -> "Cart":
        if not data:
            return cls()
        try:
            return cls(
                end=date.fromisoformat(data["end"]) if data.get("end") else None,
                lines=[Line(**line) for line in data.get("lines", [])],
                borrower=data.get("borrower"),
                confirmed=bool(data.get("confirmed")),
                reservation=data.get("reservation"),
            )
        except (KeyError, TypeError, ValueError):
            return cls()


def load(request: HttpRequest) -> Cart:
    return Cart.from_session(request.session.get(SESSION_KEY))


def save(request: HttpRequest, cart: Cart) -> None:
    request.session[SESSION_KEY] = cart.to_session()


def clear(request: HttpRequest) -> None:
    request.session.pop(SESSION_KEY, None)
