"""Arten von Teilen an der Theke, erkannt am Kürzel in der EI (#127).

Festlegung (Felix, 31.10. und 03.11.2026): Eine EI hat die Form `<Präfix><Art>-<Nummer>`, ggf.
mit `-<Zusatz>`, z. B. `DAV-HL-G-143` oder `DAV-HL-H-31-B`. Das Präfix ist eine Einstellung
(`PSAGENT_EI_PREFIX`, Standard `DAV-HL-`). Bestätigte Kürzel: G (Klettergurt), KS
(Klettersteigset), H (Helm), S (Seil), EX (Expressset) und LVS (LVS-Gerät). Der Admin pflegt die
Liste, den Schnellzugriff und „nach Größe“ unter „Theke-Einstellungen“ (`counter.config`).
Ohne Einstellung stehen Klettergurt, Klettersteigset und Helm im Schnellzugriff; Gurte wählt
man nach Größe (#124).

Die Art ist kein Feld am Teil, sondern folgt aus der EI. Teile mit anderer Schreibweise gibt die
Theke über „Anderes Teil“ aus.
"""

import re

from django.conf import settings
from django.db.models import QuerySet

from counter import config
from inventory.models import Part, normalize_ei

Kind = config.Kind


def ei_prefix() -> str:
    return str(settings.PSAGENT_EI_PREFIX)


def all_kinds() -> tuple[Kind, ...]:
    """Art-Kürzel nach dem jüngsten Stand der Theke-Einstellungen."""
    return config.kinds()


def by_code(code: str) -> Kind | None:
    return next((kind for kind in all_kinds() if kind.code == code.upper()), None)


def quick_kinds() -> list[Kind]:
    return [kind for kind in all_kinds() if kind.quick]


def _pattern() -> re.Pattern[str]:
    prefix = re.escape(normalize_ei(ei_prefix()))
    return re.compile(rf"{prefix}([a-z]{{1,3}})-(\d+)(?:-[a-z0-9]+)?")


def code_of(ei: str) -> str:
    """Art-Kürzel einer EI in der Schreibweise des Vereins, sonst leer."""
    match = _pattern().fullmatch(normalize_ei(ei))
    return match.group(1).upper() if match else ""


def well_formed(ei: str) -> bool:
    return bool(code_of(ei))


def sort_key(ei: str) -> tuple[str, int, str]:
    """Nach Art und Nummer sortieren (G-9 vor G-10), sonst nach Text."""
    match = _pattern().fullmatch(normalize_ei(ei))
    if match is None:
        return ("~", 0, normalize_ei(ei))
    return (match.group(1), int(match.group(2)), normalize_ei(ei))


def parts_of(kind: Kind) -> QuerySet[Part]:
    """Alle Teile mit einem Bauteil dieser Art (auch gesperrte; frei ist eine eigene Frage)."""
    start = normalize_ei(f"{ei_prefix()}{kind.code}-")
    return Part.objects.filter(components__ei_key__startswith=start).distinct()
