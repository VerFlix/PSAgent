"""Tests für den Testdaten-Generator (Issue #3)."""

import sqlite3
from collections import Counter
from collections.abc import Sequence
from contextlib import closing
from datetime import date
from pathlib import Path, PureWindowsPath
from typing import Any

import pytest

from legacy.schema import VARIANTS
from legacy.testdata import (
    CITY,
    DEFAULT_REFERENCE_DATE,
    LAST_NAMES,
    MANUFACTURERS,
    GeneratedLegacyData,
    build_legacy_db,
)
from tests.legacy.reference import export_legacy_code, legacy_program


def query(db: Path | None, sql: str, params: Sequence[Any] = ()) -> list[tuple[Any, ...]]:
    assert db is not None
    with closing(sqlite3.connect(db)) as conn:
        return conn.execute(sql, params).fetchall()


def dump(db: Path | None) -> str:
    if db is None:
        return ""
    with closing(sqlite3.connect(db)) as conn:
        return "\n".join(conn.iterdump())


def snapshot(result: GeneratedLegacyData) -> tuple[str, str, list[tuple[str, bytes]]]:
    files = sorted((p.name, p.read_bytes()) for p in result.gal_dir.iterdir())
    return dump(result.main_db), dump(result.archive_db), files


def ei_legacy(ei: str) -> str:
    """Vergleich im Altsystem: LOWER(TRIM(...))."""
    return ei.strip().lower()


def ei_v2(ei: str) -> str:
    """Vergleich in PSAgent 2.0: ohne Groß-/Kleinschreibung und ohne Leerzeichen."""
    return "".join(ei.split()).casefold()


def all_eis(db: Path) -> list[str]:
    rows = query(db, "SELECT einzelidentifikation FROM products")
    rows += query(db, "SELECT einzelidentifikation FROM system_parts")
    return [row[0] for row in rows if row[0] and row[0].strip()]


def duplicates(values: list[str]) -> set[str]:
    return {value for value, count in Counter(values).items() if count > 1}


@pytest.fixture
def s7(tmp_path: Path) -> GeneratedLegacyData:
    return build_legacy_db(tmp_path / "s7", "s7", seed=7)


@pytest.fixture
def s7_clean(tmp_path: Path) -> GeneratedLegacyData:
    return build_legacy_db(tmp_path / "s7_sauber", "s7", seed=7, edge_cases=False)


# --- Reproduzierbarkeit und Schutz vor Überschreiben ---


def test_same_seed_gives_identical_data(tmp_path: Path) -> None:
    first = build_legacy_db(tmp_path / "a", "s7", seed=42)
    second = build_legacy_db(tmp_path / "b", "s7", seed=42)

    assert snapshot(first) == snapshot(second)


def test_different_seed_gives_different_data(tmp_path: Path) -> None:
    first = build_legacy_db(tmp_path / "a", "s7", seed=1)
    second = build_legacy_db(tmp_path / "b", "s7", seed=2)

    assert dump(first.main_db) != dump(second.main_db)


@pytest.mark.parametrize("existing", ["psa.db", "psa_archiv.db", "GAL"])
def test_refuses_to_overwrite_existing_files(tmp_path: Path, existing: str) -> None:
    target = tmp_path / "ziel"
    target.mkdir()
    if existing == "GAL":
        (target / existing).mkdir()
    else:
        (target / existing).write_bytes(b"nicht anfassen")
    before = sorted(p.name for p in target.iterdir())

    with pytest.raises(FileExistsError):
        build_legacy_db(target, "s7", seed=1)

    assert sorted(p.name for p in target.iterdir()) == before
    if existing != "GAL":
        assert (target / existing).read_bytes() == b"nicht anfassen"


# --- Inhalt ---


@pytest.mark.parametrize("name", list(VARIANTS))
def test_every_table_of_every_variant_gets_rows(name: str, tmp_path: Path) -> None:
    result = build_legacy_db(tmp_path / name, name, seed=3)

    for db in (result.main_db, result.archive_db):
        if db is None:
            continue
        tables = query(db, "SELECT name FROM sqlite_master WHERE type = 'table'")
        for (table,) in tables:
            if table != "sqlite_sequence":
                assert query(db, f'SELECT COUNT(*) FROM "{table}"')[0][0] > 0, (db.name, table)  # noqa: S608


def test_data_is_recognisably_artificial(s7: GeneratedLegacyData) -> None:
    people = query(
        s7.main_db,
        "SELECT entleiher FROM verleih_planung UNION SELECT ausgebende_person FROM verleih_planung "
        "UNION SELECT ruecknehmende_person FROM verleih_planung "
        "UNION SELECT sachkundiger_name FROM psa_pruefungsberichte",
    )
    for (person,) in people:
        assert person is None or person.split()[-1] in LAST_NAMES

    contacts = query(
        s7.main_db,
        "SELECT entleiher_email, entleiher_telefon, entleiher_adresse FROM verleih_planung",
    )
    for email, phone, address in contacts:
        assert email.endswith("@example.org")
        assert phone.startswith("0000 ")
        assert address.endswith(CITY)

    for (manufacturer,) in query(s7.main_db, "SELECT DISTINCT hersteller FROM products"):
        assert manufacturer in MANUFACTURERS


def test_clean_data_has_plausible_dates(s7_clean: GeneratedLegacyData) -> None:
    today = DEFAULT_REFERENCE_DATE.isoformat()
    rows = query(
        s7_clean.main_db, "SELECT herstellungsjahr, kaufdatum, datum_einsatz FROM products"
    )
    for year, bought, first_use in rows:
        date.fromisoformat(bought)
        date.fromisoformat(first_use)
        assert int(year) <= int(bought[:4])
        assert bought <= first_use < today


def test_reference_date_shifts_all_dates(tmp_path: Path) -> None:
    later = date(2030, 1, 15)
    result = build_legacy_db(tmp_path / "x", "s7", seed=1, edge_cases=False, reference_date=later)

    newest = query(result.main_db, "SELECT MAX(von_datum) FROM verleih_planung")[0][0]
    assert newest > later.isoformat()


# --- Randfälle ---


def test_edge_cases_contain_ei_duplicates_that_legacy_misses(s7: GeneratedLegacyData) -> None:
    eis = all_eis(s7.main_db)
    legacy = duplicates([ei_legacy(ei) for ei in eis])
    v2 = duplicates([ei_v2(ei) for ei in eis])

    assert {"tst-0900", "tst-0901", "tst-0903", "tst-0904"} <= legacy
    assert "tst0902" in v2  # „TST 0902“ und „TST0902“: im Altsystem verschieden
    assert "tst 0902" not in legacy
    assert query(s7.main_db, "SELECT COUNT(*) FROM products WHERE einzelidentifikation = ''")[0][0]


def test_clean_data_has_unique_eis(s7_clean: GeneratedLegacyData) -> None:
    assert duplicates([ei_v2(ei) for ei in all_eis(s7_clean.main_db)]) == set()


def test_edge_cases_contain_unusual_dates(s7: GeneratedLegacyData) -> None:
    bought = {row[0] for row in query(s7.main_db, "SELECT kaufdatum FROM products")}
    years = {row[0] for row in query(s7.main_db, "SELECT herstellungsjahr FROM products")}
    checks = {row[0] for row in query(s7.main_db, "SELECT naechste_pruefung_am FROM products")}

    assert {"01.03.2021", "2021-02-30"} <= bought
    assert "unbekannt" in years
    assert "31.12.2026" in checks


def test_edge_cases_contain_problematic_loans(s7: GeneratedLegacyData) -> None:
    today = DEFAULT_REFERENCE_DATE.isoformat()
    overdue = query(
        s7.main_db,
        "SELECT COUNT(*) FROM verleih_planung WHERE status = 'lent' AND rueckgabe_datum < ?",
        (today,),
    )
    overlapping = query(
        s7.main_db,
        "SELECT COUNT(*) FROM verleih_planung a JOIN verleih_planung b "
        "ON a.item_type = b.item_type AND a.item_id = b.item_id AND a.id < b.id "
        "WHERE a.status IN ('lent', 'reserved') AND b.status IN ('lent', 'reserved') "
        "AND a.von_datum <= b.rueckgabe_datum AND b.von_datum <= a.rueckgabe_datum",
    )
    empty_status = query(s7.main_db, "SELECT COUNT(*) FROM verleih_planung WHERE status = ''")
    after_check = query(
        s7.main_db,
        "SELECT COUNT(*) FROM verleih_planung v JOIN products p "
        "ON v.item_type = 'product' AND v.item_id = p.id "
        "WHERE v.status = 'reserved' AND v.rueckgabe_datum > p.naechste_pruefung_am",
    )

    assert overdue[0][0] > 0
    assert overlapping[0][0] > 0
    assert empty_status[0][0] == 2
    assert after_check[0][0] > 0


def test_edge_cases_contain_incomplete_systems(s7: GeneratedLegacyData) -> None:
    parts_per_system = dict(
        query(s7.main_db, "SELECT system_id, COUNT(*) FROM system_parts GROUP BY system_id")
    )
    systems_without_ei = query(
        s7.main_db,
        "SELECT s.id FROM systems s WHERE NOT EXISTS (SELECT 1 FROM system_parts p "
        "WHERE p.system_id = s.id AND TRIM(COALESCE(p.einzelidentifikation, '')) <> '')",
    )
    empty_parts = query(
        s7.main_db,
        "SELECT COUNT(*) FROM system_parts WHERE produktbezeichnung = '' AND hersteller = ''",
    )

    assert 1 in parts_per_system.values()
    assert systems_without_ei
    assert empty_parts[0][0] > 0


def gal_file_name(gal_datei: str) -> str:
    return PureWindowsPath(gal_datei).name


def test_edge_cases_reference_missing_and_orphaned_gal_files(s7: GeneratedLegacyData) -> None:
    present = {p.name for p in s7.gal_dir.iterdir()}
    referenced = {
        gal_file_name(row[0])
        for row in query(
            s7.main_db, "SELECT gal_datei FROM products UNION SELECT gal_datei FROM systems"
        )
        if row[0]
    }

    assert referenced - present  # Verweise ohne Datei
    assert present - referenced  # Dateien ohne Verweis


def test_clean_data_has_all_gal_files(s7_clean: GeneratedLegacyData) -> None:
    present = {p.name for p in s7_clean.gal_dir.iterdir()}
    for (gal_datei,) in query(
        s7_clean.main_db, "SELECT gal_datei FROM products UNION SELECT gal_datei FROM systems"
    ):
        assert gal_file_name(gal_datei) in present


def test_clean_data_has_no_empty_status(s7_clean: GeneratedLegacyData) -> None:
    assert (
        query(s7_clean.main_db, "SELECT COUNT(*) FROM verleih_planung WHERE status = ''")[0][0] == 0
    )


# --- Archiv wie im Altsystem ---


def test_archived_items_follow_legacy_behaviour(s7: GeneratedLegacyData) -> None:
    archived = query(s7.archive_db, "SELECT item_type, source_item_id FROM archived_items")
    assert len(archived) == 3

    for item_type, item_id in archived:
        table = "products" if item_type == "product" else "systems"
        in_main = query(s7.main_db, f"SELECT COUNT(*) FROM {table} WHERE id = ?", (item_id,))  # noqa: S608
        in_archive = query(s7.archive_db, f"SELECT COUNT(*) FROM {table} WHERE id = ?", (item_id,))  # noqa: S608
        loans_main = query(
            s7.main_db,
            "SELECT COUNT(*) FROM verleih_planung WHERE item_type = ? AND item_id = ?",
            (item_type, item_id),
        )
        assert in_main[0][0] == 0
        assert in_archive[0][0] == 1
        assert loans_main[0][0] > 0  # bleiben in der Hauptdatenbank stehen

    actions = query(
        s7.main_db,
        "SELECT COUNT(*) FROM psa_pruefungsbericht_items WHERE aktion = 'archived+delete'",
    )
    assert actions[0][0] == 3


def test_variants_without_archive_keep_all_items(tmp_path: Path) -> None:
    result = build_legacy_db(tmp_path / "s4", "s4", seed=7)
    with_archive = build_legacy_db(tmp_path / "s7", "s7", seed=7)

    assert result.archive_db is None
    count = query(result.main_db, "SELECT COUNT(*) FROM products")[0][0]
    assert count == query(with_archive.main_db, "SELECT COUNT(*) FROM products")[0][0] + 2


# --- Das Altsystem kann die Testdaten verarbeiten ---


def test_legacy_code_processes_generated_data(s7: GeneratedLegacyData, tmp_path: Path) -> None:
    code = export_legacy_code(tmp_path / "code")
    with legacy_program(code, tmp_path / "arbeit") as modules:
        psa, verleih = modules["psa_gui"], modules["verleih_gui"]

        assert psa.fetch_due_items(s7.main_db)
        assert psa.fetch_reports(s7.main_db)
        assert len(psa.fetch_archived_items(s7.archive_db)) == 3
        assert verleih.fetch_products(s7.main_db)
        assert verleih.fetch_pending_verleihplaene(s7.main_db)
        assert psa.find_einzelidentifikation_conflict(s7.main_db, " tst-0001 ")
        assert psa.find_einzelidentifikation_conflict(s7.main_db, "TST0902") is not None
        assert psa.find_einzelidentifikation_conflict(s7.main_db, "TST 0902") is not None

        verleih.ensure_verleih_schema(s7.main_db)  # setzt leeren Status auf reserved/lent

    statuses = {row[0] for row in query(s7.main_db, "SELECT DISTINCT status FROM verleih_planung")}
    assert statuses == {"reserved", "lent", "returned"}
