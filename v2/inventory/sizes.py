"""Größen von Bauteilen, z. B. Gurten (#124).

Festlegung (Felix, 31.10. und 02.11.2026): XS bis XL, Bereiche daraus für verstellbare Gurte
(gespeichert als „M-XL“, angezeigt als „M–XL“) und die Kindergurte „K1“ und „K2“. Leer heißt
„ohne Angabe“. Die Größe gehört nicht zur EI.
"""

ADULT = ("XS", "S", "M", "L", "XL")
KIDS = {"K1": "Kindergurt 1", "K2": "Kindergurt 2"}
_RANGES = [
    f"{low}-{high}" for low_index, low in enumerate(ADULT) for high in ADULT[low_index + 1 :]
]
SIZE_CHOICES: list[tuple[str, str]] = [
    *[(size, size) for size in ADULT],
    *[(value, value.replace("-", "–")) for value in _RANGES],
    *KIDS.items(),
]
SIZES = [value for value, _ in SIZE_CHOICES]


def size_label(size: str) -> str:
    """Anzeige einer gespeicherten Größe; leer bleibt leer."""
    return dict(SIZE_CHOICES).get(size, size)


def _bounds(size: str) -> tuple[int, int] | None:
    if size in ADULT:
        index = ADULT.index(size)
        return index, index
    if size in _RANGES:
        low, high = size.split("-")
        return ADULT.index(low), ADULT.index(high)
    return None


def covers(size: str, wanted: str) -> bool:
    """Passt ein Bauteil mit `size` für jemanden mit Größe `wanted`? Bereiche decken jede
    enthaltene Größe ab; Kindergurte passen nur zu sich selbst."""
    if not size or not wanted:
        return False
    if wanted in KIDS or size in KIDS:
        return size == wanted
    bounds, target = _bounds(size), _bounds(wanted)
    return bounds is not None and target is not None and bounds[0] <= target[0] <= bounds[1]


def sizes_covering(wanted: str) -> list[str]:
    """Alle gespeicherten Werte, die `wanted` abdecken; genau passende zuerst, dann die engsten
    Bereiche (für Vorschläge an der Theke, #127)."""
    matching = [size for size in SIZES if covers(size, wanted)]

    def width(size: str) -> tuple[int, int]:
        bounds = _bounds(size)
        return (0, 0) if bounds is None else (bounds[1] - bounds[0], bounds[0])

    return sorted(matching, key=width)
