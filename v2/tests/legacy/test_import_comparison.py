"""Alt- und Neuabfragen stimmen überein (Konzept, Abschnitt 4; Abnahme Phase 1).

Nach dem Import laufen dieselben Abfragen gegen Alt- und Neudaten: Fälligkeitsliste,
Verfügbarkeit an Stichtagen und Anzahl je Tabelle. Abweichungen sind nur erlaubt, wo PSAgent 2.0
bewusst anders entscheidet (`docs/altsystem-import.md`); jede braucht einen benannten Grund.
"""

import sqlite3
from collections import Counter
from collections.abc import Iterator
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from inspections.services import next_inspection_due
from inventory.models import Part
from legacy.importer import ImportReport, run_import
from lending.services import unavailability_reasons
from tests.legacy.conftest import Generate
from tests.legacy.reference import export_legacy_code, legacy_program

pytestmark = pytest.mark.django_db

# Varianten, die der aktuelle Altsystem-Code abfragen kann (alle Spalten nachgerüstet).
VARIANTS = (
    "s7",
    "s6_aktualisiert",
    "s5_aktualisiert",
    "s3_aktualisiert",
    "s2_aktualisiert",
    "echt_2026_10_15",
)
PERIODS = ((0, 0), (1, 3), (5, 12), (20, 35), (60, 61))

# Gründe, aus denen PSAgent 2.0 bewusst anders entscheidet (docs/altsystem-verhalten.md, #9).
RETURN_AFTER_CHECK = "Rückgabe nach der nächsten Prüfung prüft das Altsystem nur in der Oberfläche"
OVERDUE = "überfälliges Teil bleibt in PSAgent 2.0 blockiert"
EMPTY_STATUS = "leerer Status blockiert im Altsystem nicht"
GERMAN_DATE = "Datum TT.MM.JJJJ: im Altsystem Textvergleich"
REJECTED_OVERLAP = "abgelehnte überschneidende Ausleihe"


def _sqlite_today(db: Path) -> date:
    with closing(sqlite3.connect(db)) as conn:
        return date.fromisoformat(conn.execute("SELECT DATE('now')").fetchone()[0])


def _parse(raw: str) -> date | None:
    for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


@dataclass
class Compared:
    db: Path
    today: date
    report: ImportReport
    psa: ModuleType
    verleih: ModuleType

    def rows(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with closing(sqlite3.connect(self.db)) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(row) for row in conn.execute(sql, params)]


@pytest.fixture(params=VARIANTS)
def compared(
    request: pytest.FixtureRequest,
    fresh_source: Generate,
    tmp_path: Path,
    media_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Compared]:
    probe = tmp_path / "probe.db"
    today = _sqlite_today(probe)
    probe.unlink(missing_ok=True)
    source = fresh_source(request.param, reference_date=today)
    # Beide Seiten rechnen mit demselben „heute“ (das Altsystem: DATE('now') in UTC).
    monkeypatch.setattr("lending.services.today", lambda: today)
    report = run_import(source.main_db.parent, dry_run=False)
    code = export_legacy_code(tmp_path / "altsystem-code")
    with legacy_program(code, tmp_path / "arbeitsordner") as modules:
        yield Compared(
            db=source.main_db,
            today=today,
            report=report,
            psa=modules["psa_gui"],
            verleih=modules["verleih_gui"],
        )


def _part(item_type: str, item_id: int) -> Part | None:
    return Part.objects.filter(legacy_kind=item_type, legacy_id=item_id).first()


def test_counts_per_table_match(compared: Compared) -> None:
    """Anzahl je Tabelle: Der Import liest jede Zeile der Hauptdatenbank."""
    for table, counts in compared.report.counts.items():
        present = compared.rows(
            "SELECT COUNT(*) AS n FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
        )[0]["n"]
        if table == "archived_items" or not present:
            continue
        # Tabellennamen kommen aus der festen Liste des Imports.
        legacy_rows = compared.rows(f'SELECT COUNT(*) AS n FROM "{table}"')[0]["n"]  # noqa: S608
        if table in ("products", "systems"):
            assert counts.read == legacy_rows, table
        else:
            assert counts.read >= legacy_rows, table  # dazu Zeilen aus dem Archiv


def test_due_list_matches(compared: Compared) -> None:
    """Fälligkeitsliste: gleiche nächste Prüfung und gleicher Sperrstatus je Teil."""
    checked = 0
    for row in compared.psa.fetch_due_items(compared.db):
        part = _part(row["item_type"], row["item_id"])
        if part is None:
            continue  # abgelehnt; steht mit Grund im Bericht
        assert part.is_locked() is bool(row["is_locked"]), row
        assert next_inspection_due(part) == _parse(row["naechste_pruefung_am"].strip()), row
        checked += 1
    assert checked > 20


def _explain(
    compared: Compared, part: Part, start: date, end: date, reasons: list[str]
) -> str | None:
    """Benennt den Grund, warum PSAgent 2.0 hier bewusst anders entscheidet."""
    table = "products" if part.legacy_kind == "product" else "systems"
    raw = compared.rows(
        f"SELECT naechste_pruefung_am FROM {table} WHERE id = ?",  # noqa: S608
        (part.legacy_id,),
    )[0]["naechste_pruefung_am"]
    raw = (raw or "").strip()
    if raw and _parse(raw) is not None and "-" not in raw:
        return GERMAN_DATE
    explained = []
    for reason in reasons:
        if "vor der nächsten PSA-Prüfung" in reason:
            explained.append(RETURN_AFTER_CHECK)
        elif "noch nicht zurückgegeben" in reason:
            explained.append(OVERDUE)
        elif "bereits vergeben" in reason:
            loans = compared.rows(
                "SELECT status FROM verleih_planung WHERE item_type = ? AND item_id = ? "
                "AND von_datum <= ? AND rueckgabe_datum >= ?",
                (part.legacy_kind, part.legacy_id, end.isoformat(), start.isoformat()),
            )
            if any(loan["status"] == "" for loan in loans):
                explained.append(EMPTY_STATUS)
            else:
                return None
        else:
            return None
    if not reasons:
        rejected = [
            line
            for line in compared.report.rejected
            if "überschneidet" in line and part.components.first().ei in line  # type: ignore[union-attr]
        ]
        return REJECTED_OVERLAP if rejected else None
    return "; ".join(sorted(set(explained)))


def test_availability_matches_on_reference_days(compared: Compared) -> None:
    """Verfügbarkeit an Stichtagen: gleich, außer wo PSAgent 2.0 bewusst strenger ist."""
    deviations: Counter[str] = Counter()
    same = 0
    parts = Part.objects.filter(legacy_id__isnull=False).exclude(events__kind="retired")
    for part in parts:
        for first, last in PERIODS:
            start, end = compared.today + timedelta(first), compared.today + timedelta(last)
            legacy = bool(
                compared.verleih.is_item_available(
                    compared.db,
                    item_type=part.legacy_kind,
                    item_id=part.legacy_id,
                    von_datum=start.isoformat(),
                    bis_datum=end.isoformat(),
                )
            )
            reasons = unavailability_reasons(part, start, end)
            if legacy == (not reasons):
                same += 1
                continue
            reason = _explain(compared, part, start, end, reasons)
            assert reason is not None, (
                part.legacy_kind,
                part.legacy_id,
                start,
                end,
                legacy,
                reasons,
            )
            deviations[reason] += 1
    assert same > 0
    allowed = {RETURN_AFTER_CHECK, OVERDUE, EMPTY_STATUS, GERMAN_DATE, REJECTED_OVERLAP}
    assert {part for reason in deviations for part in reason.split("; ")} <= allowed
