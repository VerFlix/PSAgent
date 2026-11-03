"""Einstellungen der Theke: Art-Kürzel und Vorauswahl der Leihdauer (#127, Teil 3).

Festlegung (Felix, 03.11.2026): Der Admin pflegt Kürzel, Name, Schnellzugriff und „nach Größe“
(z. B. später für Helme) sowie die Vorauswahl der Dauer. Jede Änderung bestätigt er mit dem
YubiKey (`counter.views_settings`); gespeichert wird nur angehängt (`CounterSetting`). Ohne
Eintrag gelten die Startwerte unten (Festlegung vom 31.10. und 03.11.2026).
"""

import re
from collections.abc import Sequence
from dataclasses import asdict, dataclass

from accounts.models import User
from accounts.rights import Right, require
from counter.models import CounterSetting


@dataclass(frozen=True)
class Kind:
    code: str
    label: str
    quick: bool = False  # Schnellzugriff in Schritt „Teile“
    sized: bool = False  # Auswahl nach Größe (#124), nur mit Schnellzugriff


DEFAULT_KINDS: tuple[Kind, ...] = (
    Kind("G", "Klettergurt", quick=True, sized=True),
    Kind("KS", "Klettersteigset", quick=True),
    Kind("H", "Helm", quick=True),
    Kind("S", "Seil"),
    Kind("EX", "Expressset"),
    Kind("LVS", "LVS-Gerät"),
)
DURATIONS = ((1, "Morgen"), (3, "3 Tage"), (7, "1 Woche"), (14, "2 Wochen"))
DEFAULT_DAYS = 7  # wie im Prototyp
MAX_KINDS = 24
MAX_QUICK = 4  # mehr Karten passen im Querformat nicht nebeneinander
CODE = re.compile(r"[A-Z]{1,3}")


class SettingError(ValueError):
    """Eingabe abgewiesen; der Text sagt, warum."""


def current() -> CounterSetting | None:
    return CounterSetting.objects.first()


def kinds() -> tuple[Kind, ...]:
    setting = current()
    if setting is None:
        return DEFAULT_KINDS
    return tuple(Kind(**entry) for entry in setting.kinds)


def default_days() -> int:
    setting = current()
    return setting.default_days if setting else DEFAULT_DAYS


def clean(entries: Sequence[Kind]) -> list[Kind]:
    """Prüft die Liste: Kürzel 1–3 Buchstaben, eindeutig, Name nötig, „nach Größe“ nur mit
    Schnellzugriff, höchstens vier im Schnellzugriff."""
    result: list[Kind] = []
    seen: set[str] = set()
    for entry in entries:
        code = entry.code.strip().upper()
        label = entry.label.strip()
        if not CODE.fullmatch(code):
            raise SettingError(f"„{entry.code}“: Ein Kürzel hat 1 bis 3 Buchstaben (A–Z).")
        if code in seen:
            raise SettingError(f"Das Kürzel {code} steht doppelt in der Liste.")
        if not label:
            raise SettingError(f"Für {code} fehlt der Name.")
        if entry.sized and not entry.quick:
            raise SettingError(f"{code}: „nach Größe“ geht nur zusammen mit dem Schnellzugriff.")
        seen.add(code)
        result.append(Kind(code, label[:40], quick=entry.quick, sized=entry.sized))
    if len(result) > MAX_KINDS:
        raise SettingError(f"Höchstens {MAX_KINDS} Art-Kürzel.")
    if sum(kind.quick for kind in result) > MAX_QUICK:
        raise SettingError(f"Höchstens {MAX_QUICK} Arten im Schnellzugriff.")
    return result


def configure(entries: Sequence[Kind], days: int, *, actor: User | None) -> CounterSetting:
    """Neuer Stand; nur Admin (die Ansicht verlangt zusätzlich den YubiKey)."""
    require(actor, Right.COUNTER_SETTINGS)
    if days not in {d for d, _ in DURATIONS}:
        raise SettingError("Bitte eine der Dauern als Vorauswahl wählen.")
    cleaned = clean(entries)
    return CounterSetting.objects.create(
        kinds=[asdict(kind) for kind in cleaned], default_days=days, actor=actor
    )
