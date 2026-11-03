"""Freie Teile für die Ausgabe an der Theke (#127).

Keine neue Fachlogik: Frei ist, was `lending.services.unavailability_reasons` für den Zeitraum
von heute bis zur Rückgabe nicht beanstandet (gesperrt, PSA-Prüfung, Überschneidung).
"""

from collections.abc import Collection, Iterable
from dataclasses import dataclass
from datetime import date
from urllib.parse import unquote

from counter import kinds
from counter.kinds import Kind
from inventory.models import Component, Part, normalize_ei
from inventory.sizes import size_label, sizes_covering
from lending import services

NO_SIZE = ""  # Gurte ohne Größenangabe


@dataclass(frozen=True)
class Offer:
    """Ein freies Teil einer Art, mit der EI und Größe seines Bauteils dieser Art."""

    part: Part
    ei: str
    size: str


def _component(part: Part, kind: Kind | None = None) -> Component | None:
    components = list(part.components.all())
    for component in components:
        if kind is None or kinds.code_of(component.ei) == kind.code:
            return component
    return components[0] if components else None


def reasons(part: Part, end: date) -> list[str]:
    """Gründe gegen eine Ausgabe von heute bis `end`; leer heißt frei."""
    return services.unavailability_reasons(part, services.today(), end)


def offers(kind: Kind, end: date) -> list[Offer]:
    """Freie Teile der Art, nach EI-Nummer sortiert."""
    found = []
    for part in kinds.parts_of(kind).prefetch_related("components"):
        component = _component(part, kind)
        if component is None or reasons(part, end):
            continue
        found.append(Offer(part, component.ei, component.size))
    return sorted(found, key=lambda offer: kinds.sort_key(offer.ei))


def fits(offer: Offer, wanted: str) -> bool:
    if wanted == NO_SIZE:
        return offer.size == ""
    return offer.size in sizes_covering(wanted)


def for_size(available: Iterable[Offer], wanted: str, taken: Collection[int]) -> list[Offer]:
    """Passende freie Gurte: genau passende zuerst, dann die engsten Bereiche (#124)."""
    order = [NO_SIZE] if wanted == NO_SIZE else sizes_covering(wanted)
    matching = [o for o in available if o.part.pk not in taken and fits(o, wanted)]
    return sorted(matching, key=lambda o: (order.index(o.size), kinds.sort_key(o.ei)))


def describe_size(size: str) -> str:
    if not size:
        return "ohne Größenangabe"
    return f"verstellbar {size_label(size)}" if "-" in size else f"Größe {size_label(size)}"


def find(raw: str) -> Component | None:
    """Bauteil zu einer eingegebenen oder gescannten EI (ohne Groß-/Kleinschreibung)."""
    value = raw.strip()
    if "/q/" in value:  # QR-Etikett: Link „…/q/<EI>“ mit kodierter EI (#14)
        value = unquote(value.rsplit("/q/", 1)[1])
    if not value:
        return None
    return Component.objects.select_related("part").filter(ei_key=normalize_ei(value)).first()


def suggest(raw: str, limit: int = 3) -> list[Component]:
    """EIs, die mit der Eingabe beginnen (ab dem ersten Zeichen nach dem Präfix)."""
    key = normalize_ei(raw)
    if len(key) <= len(normalize_ei(kinds.ei_prefix())):
        return []
    matches = (
        Component.objects.select_related("part").filter(ei_key__startswith=key).order_by("ei_key")
    )
    return sorted(matches[:50], key=lambda c: kinds.sort_key(c.ei))[:limit]
