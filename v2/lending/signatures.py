"""Unterschriften als SVG anzeigen (Parität 19): aus der Strichliste [[[x, y], …], …]."""

from typing import Any

# Mindestgröße der Zeichenfläche; das Unterschriftsfeld der App ist 600 x 200.
MIN_WIDTH, MIN_HEIGHT = 600, 200


def svg(strokes: Any) -> dict[str, Any]:
    """`viewbox` und je Strich ein SVG-Pfad; nur Zahlen, nichts aus der Eingabe als Text."""
    points = [
        (float(x), float(y))
        for stroke in strokes or []
        if isinstance(stroke, list)
        for point in stroke
        if isinstance(point, list | tuple) and len(point) == 2
        for x, y in [point]
        if isinstance(x, int | float) and isinstance(y, int | float)
    ]
    width = max([MIN_WIDTH, *(x for x, _ in points)])
    height = max([MIN_HEIGHT, *(y for _, y in points)])
    paths = []
    for stroke in strokes or []:
        coords = [
            f"{float(x):g} {float(y):g}"
            for point in (stroke if isinstance(stroke, list) else [])
            if isinstance(point, list | tuple) and len(point) == 2
            for x, y in [point]
            if isinstance(x, int | float) and isinstance(y, int | float)
        ]
        if coords:
            paths.append("M " + " L ".join(coords))
    return {"viewbox": f"0 0 {width:g} {height:g}", "paths": paths}
