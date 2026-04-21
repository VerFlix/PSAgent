import json
import sqlite3
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import shutil
import calendar
from datetime import date, datetime
from pathlib import Path

from generate_PDF import (
    DEFAULT_DB_PATH,
    init_db,
    generate_einsatzdokumentation_pdf_from_db,
    generate_pd_pdf_from_db,
    generate_haftungsausschluss_pdf_from_db,
)

GAL_DIR = Path("GAL")
GAL_DIR.mkdir(exist_ok=True)
ARCHIVE_DB_PATH = Path("psa_archiv.db")

PRODUCT_FIELDS = [
    ("Produktbezeichnung", "produktbezeichnung"),
    ("gem. EN", "gem_en"),
    ("Produktname", "produktname"),
    ("Hersteller", "hersteller"),
    ("Herstellungsjahr", "herstellungsjahr"),
    ("Kaufdatum", "kaufdatum"),
    ("Datum 1. Einsatz", "datum_einsatz"),
    ("Einzelidentifikation", "einzelidentifikation"),
    ("Seriennummer", "seriennummer"),
]


def _today_iso() -> str:
    return date.today().isoformat()


def _add_months_iso(start_iso: str, months: int) -> str:
    d = datetime.strptime(start_iso, "%Y-%m-%d").date()
    m = d.month - 1 + months
    y = d.year + m // 12
    m = m % 12 + 1
    day = min(d.day, calendar.monthrange(y, m)[1])
    return date(y, m, day).isoformat()


def _db_connect(db_path: Path):
    return sqlite3.connect(db_path)


def ensure_db_schema(db_path: Path) -> None:
    """Fügt fehlende Spalten hinzu, falls eine alte DB verwendet wird."""
    with _db_connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS item_locks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_type TEXT NOT NULL,
                item_id INTEGER NOT NULL,
                is_locked INTEGER NOT NULL DEFAULT 1,
                lock_comment TEXT,
                unlock_comment TEXT,
                lock_at TEXT,
                unlock_at TEXT,
                unlock_source TEXT,
                UNIQUE(item_type, item_id)
            )
            """
        )

        lock_cols = {row[1] for row in conn.execute("PRAGMA table_info(item_locks)")}
        if "is_locked" not in lock_cols:
            conn.execute("ALTER TABLE item_locks ADD COLUMN is_locked INTEGER NOT NULL DEFAULT 1")
        if "lock_comment" not in lock_cols:
            conn.execute("ALTER TABLE item_locks ADD COLUMN lock_comment TEXT")
        if "unlock_comment" not in lock_cols:
            conn.execute("ALTER TABLE item_locks ADD COLUMN unlock_comment TEXT")
        if "lock_at" not in lock_cols:
            conn.execute("ALTER TABLE item_locks ADD COLUMN lock_at TEXT")
        if "unlock_at" not in lock_cols:
            conn.execute("ALTER TABLE item_locks ADD COLUMN unlock_at TEXT")
        if "unlock_source" not in lock_cols:
            conn.execute("ALTER TABLE item_locks ADD COLUMN unlock_source TEXT")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS verleih_planung (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_type TEXT NOT NULL,
                item_id INTEGER NOT NULL,
                item_label TEXT,
                von_datum TEXT NOT NULL,
                rueckgabe_datum TEXT NOT NULL,
                entleiher TEXT,
                ausgebende_person TEXT,
                entleiher_email TEXT,
                entleiher_telefon TEXT,
                entleiher_adresse TEXT,
                signature_data TEXT,
                quick_check_out INTEGER NOT NULL DEFAULT 0,
                gal_provided_out INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'lent',
                checkout_at TEXT,
                return_comment TEXT,
                ruecknehmende_person TEXT,
                return_signature_data TEXT,
                quick_check_return INTEGER NOT NULL DEFAULT 0,
                returned_at TEXT,
                created_at TEXT NOT NULL
            )
            """
        )

        verleih_cols = {row[1] for row in conn.execute("PRAGMA table_info(verleih_planung)")}
        if "status" not in verleih_cols:
            conn.execute("ALTER TABLE verleih_planung ADD COLUMN status TEXT NOT NULL DEFAULT 'lent'")
        if "checkout_at" not in verleih_cols:
            conn.execute("ALTER TABLE verleih_planung ADD COLUMN checkout_at TEXT")
        if "returned_at" not in verleih_cols:
            conn.execute("ALTER TABLE verleih_planung ADD COLUMN returned_at TEXT")
        if "entleiher_email" not in verleih_cols:
            conn.execute("ALTER TABLE verleih_planung ADD COLUMN entleiher_email TEXT")
        if "entleiher_telefon" not in verleih_cols:
            conn.execute("ALTER TABLE verleih_planung ADD COLUMN entleiher_telefon TEXT")
        if "entleiher_adresse" not in verleih_cols:
            conn.execute("ALTER TABLE verleih_planung ADD COLUMN entleiher_adresse TEXT")
        if "ausgebende_person" not in verleih_cols:
            conn.execute("ALTER TABLE verleih_planung ADD COLUMN ausgebende_person TEXT")
        if "quick_check_out" not in verleih_cols:
            conn.execute("ALTER TABLE verleih_planung ADD COLUMN quick_check_out INTEGER NOT NULL DEFAULT 0")
        if "gal_provided_out" not in verleih_cols:
            conn.execute("ALTER TABLE verleih_planung ADD COLUMN gal_provided_out INTEGER NOT NULL DEFAULT 0")
        if "return_comment" not in verleih_cols:
            conn.execute("ALTER TABLE verleih_planung ADD COLUMN return_comment TEXT")
        if "ruecknehmende_person" not in verleih_cols:
            conn.execute("ALTER TABLE verleih_planung ADD COLUMN ruecknehmende_person TEXT")
        if "return_signature_data" not in verleih_cols:
            conn.execute("ALTER TABLE verleih_planung ADD COLUMN return_signature_data TEXT")
        if "quick_check_return" not in verleih_cols:
            conn.execute("ALTER TABLE verleih_planung ADD COLUMN quick_check_return INTEGER NOT NULL DEFAULT 0")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS psa_pruefungsberichte (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                sachkundiger_name TEXT NOT NULL,
                sachkundig_bestaetigt INTEGER NOT NULL DEFAULT 0,
                signature_data TEXT NOT NULL
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS psa_pruefungsbericht_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                report_id INTEGER NOT NULL,
                item_type TEXT NOT NULL,
                item_id INTEGER,
                item_label TEXT NOT NULL,
                psa_pruefung_durchgefuehrt INTEGER NOT NULL DEFAULT 0,
                kommentar TEXT,
                naechste_pruefung_am TEXT,
                aktion TEXT,
                FOREIGN KEY(report_id) REFERENCES psa_pruefungsberichte(id)
            )
            """
        )

        product_cols = {row[1] for row in conn.execute("PRAGMA table_info(products)")}
        system_cols = {row[1] for row in conn.execute("PRAGMA table_info(systems)")}

        if "gal_datei" not in product_cols:
            conn.execute("ALTER TABLE products ADD COLUMN gal_datei TEXT")
        if "gal_link" not in product_cols:
            conn.execute("ALTER TABLE products ADD COLUMN gal_link TEXT")
        if "naechste_pruefung_am" not in product_cols:
            conn.execute("ALTER TABLE products ADD COLUMN naechste_pruefung_am TEXT")
        if "last_psa_pruefung_am" not in product_cols:
            conn.execute("ALTER TABLE products ADD COLUMN last_psa_pruefung_am TEXT")
        if "last_psa_pruefung_kommentar" not in product_cols:
            conn.execute("ALTER TABLE products ADD COLUMN last_psa_pruefung_kommentar TEXT")
        if "last_psa_durchgefallen_am" not in product_cols:
            conn.execute("ALTER TABLE products ADD COLUMN last_psa_durchgefallen_am TEXT")

        if "gal_datei" not in system_cols:
            conn.execute("ALTER TABLE systems ADD COLUMN gal_datei TEXT")
        if "gal_link" not in system_cols:
            conn.execute("ALTER TABLE systems ADD COLUMN gal_link TEXT")
        if "naechste_pruefung_am" not in system_cols:
            conn.execute("ALTER TABLE systems ADD COLUMN naechste_pruefung_am TEXT")
        if "last_psa_pruefung_am" not in system_cols:
            conn.execute("ALTER TABLE systems ADD COLUMN last_psa_pruefung_am TEXT")
        if "last_psa_pruefung_kommentar" not in system_cols:
            conn.execute("ALTER TABLE systems ADD COLUMN last_psa_pruefung_kommentar TEXT")
        if "last_psa_durchgefallen_am" not in system_cols:
            conn.execute("ALTER TABLE systems ADD COLUMN last_psa_durchgefallen_am TEXT")


def ensure_archive_schema(archive_db_path: Path = ARCHIVE_DB_PATH) -> None:
    init_db(archive_db_path)
    ensure_db_schema(archive_db_path)
    with _db_connect(archive_db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS archived_items (
                archived_item_id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_type TEXT NOT NULL,
                source_item_id INTEGER NOT NULL,
                item_label TEXT NOT NULL,
                archived_at TEXT NOT NULL,
                archived_by TEXT,
                kommentar TEXT
            )
            """
        )


def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()}


def _copy_rows_between_dbs(
    src_conn: sqlite3.Connection,
    dst_conn: sqlite3.Connection,
    *,
    table_name: str,
    where_sql: str,
    where_params: tuple,
) -> int:
    src_conn.row_factory = sqlite3.Row
    rows = src_conn.execute(f"SELECT * FROM {table_name} {where_sql}", where_params).fetchall()
    if not rows:
        return 0

    dst_cols = _table_columns(dst_conn, table_name)
    first_keys = list(rows[0].keys())
    cols = [c for c in first_keys if c in dst_cols]
    if not cols:
        return 0

    col_sql = ", ".join(cols)
    placeholders = ", ".join(["?"] * len(cols))
    sql = f"INSERT OR REPLACE INTO {table_name} ({col_sql}) VALUES ({placeholders})"
    count = 0
    for row in rows:
        dst_conn.execute(sql, tuple(row[c] for c in cols))
        count += 1
    return count


def archive_item_with_history(
    db_path: Path,
    *,
    item_type: str,
    item_id: int,
    item_label: str,
    kommentar: str,
    archived_by: str,
    archive_db_path: Path = ARCHIVE_DB_PATH,
) -> bool:
    ensure_archive_schema(archive_db_path)

    with _db_connect(db_path) as src, _db_connect(archive_db_path) as dst:
        src.row_factory = sqlite3.Row

        if item_type == "product":
            exists = src.execute("SELECT 1 FROM products WHERE id = ? LIMIT 1", (item_id,)).fetchone()
            if not exists:
                return False
            _copy_rows_between_dbs(src, dst, table_name="products", where_sql="WHERE id = ?", where_params=(item_id,))
        else:
            exists = src.execute("SELECT 1 FROM systems WHERE id = ? LIMIT 1", (item_id,)).fetchone()
            if not exists:
                return False
            _copy_rows_between_dbs(src, dst, table_name="systems", where_sql="WHERE id = ?", where_params=(item_id,))
            _copy_rows_between_dbs(src, dst, table_name="system_parts", where_sql="WHERE system_id = ?", where_params=(item_id,))

        _copy_rows_between_dbs(
            src,
            dst,
            table_name="verleih_planung",
            where_sql="WHERE item_type = ? AND item_id = ?",
            where_params=(item_type, item_id),
        )
        _copy_rows_between_dbs(
            src,
            dst,
            table_name="item_locks",
            where_sql="WHERE item_type = ? AND item_id = ?",
            where_params=(item_type, item_id),
        )

        report_ids = src.execute(
            """
            SELECT DISTINCT report_id
            FROM psa_pruefungsbericht_items
            WHERE item_type = ? AND item_id = ?
            """,
            (item_type, item_id),
        ).fetchall()

        for rid_row in report_ids:
            rid = int(rid_row[0])
            _copy_rows_between_dbs(src, dst, table_name="psa_pruefungsberichte", where_sql="WHERE id = ?", where_params=(rid,))
            _copy_rows_between_dbs(src, dst, table_name="psa_pruefungsbericht_items", where_sql="WHERE report_id = ?", where_params=(rid,))

        dst.execute(
            """
            INSERT INTO archived_items (
                item_type, source_item_id, item_label, archived_at, archived_by, kommentar
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                item_type,
                item_id,
                item_label,
                datetime.now().isoformat(timespec="seconds"),
                archived_by,
                kommentar,
            ),
        )
    return True


def fetch_archived_items(archive_db_path: Path = ARCHIVE_DB_PATH) -> list[dict]:
    ensure_archive_schema(archive_db_path)
    with _db_connect(archive_db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT archived_item_id, item_type, source_item_id, item_label,
                   archived_at, archived_by, kommentar
            FROM archived_items
            ORDER BY archived_at DESC, archived_item_id DESC
            """
        ).fetchall()
    return [dict(r) for r in rows]


def insert_product(db_path: Path, data: dict) -> int:
    with _db_connect(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO products (
                produktbezeichnung, gem_en, produktname, hersteller,
                herstellungsjahr, kaufdatum, datum_einsatz,
                einzelidentifikation, seriennummer, gal_datei, gal_link,
                naechste_pruefung_am
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data.get("produktbezeichnung", ""),
                data.get("gem_en", ""),
                data.get("produktname", ""),
                data.get("hersteller", ""),
                data.get("herstellungsjahr", ""),
                data.get("kaufdatum", ""),
                data.get("datum_einsatz", ""),
                data.get("einzelidentifikation", ""),
                data.get("seriennummer", ""),
                data.get("gal_datei", ""),
                data.get("gal_link", ""),
                data.get("naechste_pruefung_am", ""),
            ),
        )
        return cur.lastrowid


def insert_system(
    db_path: Path,
    name: str,
    gal_datei: str = "",
    gal_link: str = "",
    naechste_pruefung_am: str = "",
) -> int:
    with _db_connect(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO systems (name, gal_datei, gal_link, naechste_pruefung_am) VALUES (?, ?, ?, ?)",
            (name, gal_datei, gal_link, naechste_pruefung_am),
        )
        return cur.lastrowid


def insert_system_part(db_path: Path, system_id: int, part_index: int, data: dict) -> None:
    with _db_connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO system_parts (
                system_id, part_index, produktbezeichnung, gem_en, produktname,
                hersteller, herstellungsjahr, kaufdatum, datum_einsatz,
                einzelidentifikation, seriennummer
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                system_id,
                part_index,
                data.get("produktbezeichnung", ""),
                data.get("gem_en", ""),
                data.get("produktname", ""),
                data.get("hersteller", ""),
                data.get("herstellungsjahr", ""),
                data.get("kaufdatum", ""),
                data.get("datum_einsatz", ""),
                data.get("einzelidentifikation", ""),
                data.get("seriennummer", ""),
            ),
        )


def fetch_product_details(db_path: Path, product_id: int) -> dict | None:
    with _db_connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT id, produktbezeichnung, gem_en, produktname, hersteller,
                   herstellungsjahr, kaufdatum, datum_einsatz,
                     einzelidentifikation, seriennummer, gal_datei, gal_link,
                                         naechste_pruefung_am, last_psa_pruefung_am, last_psa_pruefung_kommentar,
                                         last_psa_durchgefallen_am
            FROM products
            WHERE id = ?
            """,
            (product_id,),
        ).fetchone()
    return dict(row) if row else None


def fetch_system_details(db_path: Path, system_id: int) -> dict | None:
    with _db_connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        system_row = conn.execute(
            """
            SELECT id, name, gal_datei, gal_link
                , naechste_pruefung_am, last_psa_pruefung_am, last_psa_pruefung_kommentar,
                  last_psa_durchgefallen_am
            FROM systems
            WHERE id = ?
            """,
            (system_id,),
        ).fetchone()
        if not system_row:
            return None

        part_rows = conn.execute(
            """
            SELECT part_index, produktbezeichnung, gem_en, produktname,
                   hersteller, herstellungsjahr, kaufdatum, datum_einsatz,
                   einzelidentifikation, seriennummer
            FROM system_parts
            WHERE system_id = ?
            ORDER BY part_index ASC
            """,
            (system_id,),
        ).fetchall()

    parts = {int(row["part_index"]): dict(row) for row in part_rows}
    return {"system": dict(system_row), "parts": parts}


def update_product(db_path: Path, product_id: int, data: dict) -> None:
    with _db_connect(db_path) as conn:
        conn.execute(
            """
            UPDATE products
            SET produktbezeichnung = ?,
                gem_en = ?,
                produktname = ?,
                hersteller = ?,
                herstellungsjahr = ?,
                kaufdatum = ?,
                datum_einsatz = ?,
                einzelidentifikation = ?,
                seriennummer = ?,
                gal_datei = ?,
                gal_link = ?,
                naechste_pruefung_am = ?,
                last_psa_pruefung_am = ?,
                last_psa_pruefung_kommentar = ?,
                last_psa_durchgefallen_am = ?
            WHERE id = ?
            """,
            (
                data.get("produktbezeichnung", ""),
                data.get("gem_en", ""),
                data.get("produktname", ""),
                data.get("hersteller", ""),
                data.get("herstellungsjahr", ""),
                data.get("kaufdatum", ""),
                data.get("datum_einsatz", ""),
                data.get("einzelidentifikation", ""),
                data.get("seriennummer", ""),
                data.get("gal_datei", ""),
                data.get("gal_link", ""),
                data.get("naechste_pruefung_am", ""),
                data.get("last_psa_pruefung_am", ""),
                data.get("last_psa_pruefung_kommentar", ""),
                data.get("last_psa_durchgefallen_am", ""),
                product_id,
            ),
        )


def update_system(db_path: Path, system_id: int, system_data: dict, part_data: dict[int, dict]) -> None:
    with _db_connect(db_path) as conn:
        conn.execute(
            """
            UPDATE systems
            SET name = ?,
                gal_datei = ?,
                gal_link = ?,
                naechste_pruefung_am = ?,
                last_psa_pruefung_am = ?,
                last_psa_pruefung_kommentar = ?,
                last_psa_durchgefallen_am = ?
            WHERE id = ?
            """,
            (
                system_data.get("name", ""),
                system_data.get("gal_datei", ""),
                system_data.get("gal_link", ""),
                system_data.get("naechste_pruefung_am", ""),
                system_data.get("last_psa_pruefung_am", ""),
                system_data.get("last_psa_pruefung_kommentar", ""),
                system_data.get("last_psa_durchgefallen_am", ""),
                system_id,
            ),
        )

        for part_index in (1, 2, 3):
            data = part_data.get(part_index, {})
            exists = conn.execute(
                "SELECT id FROM system_parts WHERE system_id = ? AND part_index = ?",
                (system_id, part_index),
            ).fetchone()

            if exists:
                conn.execute(
                    """
                    UPDATE system_parts
                    SET produktbezeichnung = ?,
                        gem_en = ?,
                        produktname = ?,
                        hersteller = ?,
                        herstellungsjahr = ?,
                        kaufdatum = ?,
                        datum_einsatz = ?,
                        einzelidentifikation = ?,
                        seriennummer = ?
                    WHERE system_id = ? AND part_index = ?
                    """,
                    (
                        data.get("produktbezeichnung", ""),
                        data.get("gem_en", ""),
                        data.get("produktname", ""),
                        data.get("hersteller", ""),
                        data.get("herstellungsjahr", ""),
                        data.get("kaufdatum", ""),
                        data.get("datum_einsatz", ""),
                        data.get("einzelidentifikation", ""),
                        data.get("seriennummer", ""),
                        system_id,
                        part_index,
                    ),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO system_parts (
                        system_id, part_index, produktbezeichnung, gem_en, produktname,
                        hersteller, herstellungsjahr, kaufdatum, datum_einsatz,
                        einzelidentifikation, seriennummer
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        system_id,
                        part_index,
                        data.get("produktbezeichnung", ""),
                        data.get("gem_en", ""),
                        data.get("produktname", ""),
                        data.get("hersteller", ""),
                        data.get("herstellungsjahr", ""),
                        data.get("kaufdatum", ""),
                        data.get("datum_einsatz", ""),
                        data.get("einzelidentifikation", ""),
                        data.get("seriennummer", ""),
                    ),
                )


def refresh_verleih_item_label(db_path: Path, *, item_type: str, item_id: int) -> None:
    """Aktualisiert Anzeige-Label in verleih_planung, ohne IDs zu verändern."""
    try:
        with _db_connect(db_path) as conn:
            if item_type == "product":
                row = conn.execute(
                    "SELECT einzelidentifikation, produktbezeichnung, produktname, seriennummer FROM products WHERE id = ?",
                    (item_id,),
                ).fetchone()
                if not row:
                    return
                label = f"EI: {row[0] or '-'} | PB: {row[1] or '-'} | Name: {row[2] or 'Ohne Name'} | SN: {row[3] or '-'}"
            else:
                row = conn.execute(
                    """
                    SELECT sp.einzelidentifikation, s.name
                    FROM systems s
                    LEFT JOIN system_parts sp ON sp.system_id = s.id AND sp.part_index = 1
                    WHERE s.id = ?
                    """,
                    (item_id,),
                ).fetchone()
                if not row:
                    return
                label = f"EI: {row[0] or '-'} | Name: {row[1] or 'System'}"

            conn.execute(
                """
                UPDATE verleih_planung
                SET item_label = ?
                WHERE item_type = ? AND item_id = ?
                """,
                (label, item_type, item_id),
            )
    except sqlite3.OperationalError:
        # Falls verleih_planung noch nicht existiert, nichts tun.
        return


def fetch_products(db_path: Path) -> list[tuple[int, str]]:
    with _db_connect(db_path) as conn:
        rows = conn.execute(
            """
                 SELECT p.id, p.einzelidentifikation, p.produktbezeichnung, p.produktname, p.seriennummer,
                   COALESCE(l.is_locked, 0) AS is_locked
            FROM products
            p LEFT JOIN item_locks l
              ON l.item_type = 'product' AND l.item_id = p.id
            ORDER BY COALESCE(p.einzelidentifikation, '') COLLATE NOCASE ASC,
                     p.id ASC
            """
        ).fetchall()
    result = []
    for row in rows:
        pid, ei, pb, name, sn, is_locked = row
        lock_tag = " | [GESPERRT]" if is_locked else ""
        label = f"EI: {ei or '-'} | PB: {pb or '-'} | Name: {name or 'Ohne Name'} | SN: {sn or '-'}{lock_tag}"
        result.append((pid, label))
    return result


def fetch_systems(db_path: Path) -> list[tuple[int, str]]:
    with _db_connect(db_path) as conn:
        rows = conn.execute(
            """
                     SELECT s.id, sp.einzelidentifikation, s.name,
                   COALESCE(l.is_locked, 0) AS is_locked
            FROM systems
            s LEFT JOIN system_parts sp
              ON sp.system_id = s.id AND sp.part_index = 1
            LEFT JOIN item_locks l
              ON l.item_type = 'system' AND l.item_id = s.id
            ORDER BY COALESCE(sp.einzelidentifikation, '') COLLATE NOCASE ASC,
                     s.id ASC
            """
        ).fetchall()
    result = []
    for row in rows:
        sid, ei, name, is_locked = row
        lock_tag = " | [GESPERRT]" if is_locked else ""
        label = f"EI: {ei or '-'} | Name: {name or 'System'}{lock_tag}"
        result.append((sid, label))
    return result


def unlock_item(db_path: Path, *, item_type: str, item_id: int, unlock_comment: str) -> bool:
    with _db_connect(db_path) as conn:
        cur = conn.execute(
            """
            UPDATE item_locks
            SET is_locked = 0,
                unlock_comment = ?,
                unlock_at = ?,
                unlock_source = 'psa_gui'
            WHERE item_type = ?
              AND item_id = ?
              AND is_locked = 1
            """,
                        (unlock_comment, datetime.now().isoformat(timespec="seconds"), item_type, item_id),
        )
    return cur.rowcount > 0


def lock_item(db_path: Path, *, item_type: str, item_id: int, lock_comment: str) -> bool:
    with _db_connect(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO item_locks (
                item_type, item_id, is_locked, lock_comment,
                lock_at, unlock_comment, unlock_at, unlock_source
            ) VALUES (?, ?, 1, ?, ?, NULL, NULL, NULL)
            ON CONFLICT(item_type, item_id)
            DO UPDATE SET
                is_locked = 1,
                lock_comment = excluded.lock_comment,
                lock_at = excluded.lock_at,
                unlock_comment = NULL,
                unlock_at = NULL,
                unlock_source = NULL
            """,
            (item_type, item_id, lock_comment, datetime.now().isoformat(timespec="seconds")),
        )
    return cur.rowcount > 0


def fetch_due_items(db_path: Path) -> list[dict]:
    with _db_connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT *
            FROM (
                SELECT
                    'product' AS item_type,
                    p.id AS item_id,
                    COALESCE(p.einzelidentifikation, '') AS sort_ei,
                    ('EI: ' || COALESCE(p.einzelidentifikation, '-') || ' | PB: ' || COALESCE(p.produktbezeichnung, '-') || ' | Name: ' || COALESCE(p.produktname, 'Ohne Name') || ' | SN: ' || COALESCE(p.seriennummer, '-')) AS item_label,
                    COALESCE(p.naechste_pruefung_am, '') AS naechste_pruefung_am,
                    COALESCE(p.last_psa_pruefung_am, '') AS last_psa_pruefung_am,
                    COALESCE(p.last_psa_durchgefallen_am, '') AS last_psa_durchgefallen_am,
                    COALESCE(p.last_psa_pruefung_kommentar, '') AS last_psa_pruefung_kommentar,
                    COALESCE(l.is_locked, 0) AS is_locked
                FROM products p
                LEFT JOIN item_locks l ON l.item_type = 'product' AND l.item_id = p.id

                UNION ALL

                SELECT
                    'system' AS item_type,
                    s.id AS item_id,
                    COALESCE(sp.einzelidentifikation, '') AS sort_ei,
                    ('EI: ' || COALESCE(sp.einzelidentifikation, '-') || ' | Name: ' || COALESCE(s.name, 'System')) AS item_label,
                    COALESCE(s.naechste_pruefung_am, '') AS naechste_pruefung_am,
                    COALESCE(s.last_psa_pruefung_am, '') AS last_psa_pruefung_am,
                    COALESCE(s.last_psa_durchgefallen_am, '') AS last_psa_durchgefallen_am,
                    COALESCE(s.last_psa_pruefung_kommentar, '') AS last_psa_pruefung_kommentar,
                    COALESCE(l.is_locked, 0) AS is_locked
                FROM systems s
                LEFT JOIN system_parts sp ON sp.system_id = s.id AND sp.part_index = 1
                LEFT JOIN item_locks l ON l.item_type = 'system' AND l.item_id = s.id
            ) items
            ORDER BY is_locked DESC,
                     CASE WHEN naechste_pruefung_am = '' THEN 1 ELSE 0 END,
                     naechste_pruefung_am ASC,
                     sort_ei COLLATE NOCASE ASC,
                     item_label COLLATE NOCASE ASC
            """
        ).fetchall()
    return [dict(r) for r in rows]


def fetch_item_vorgaenge(db_path: Path, *, item_type: str, item_id: int) -> list[str]:
    events: list[tuple[datetime | None, str]] = []

    def _parse_dt(value: str | None) -> datetime | None:
        if not value:
            return None
        txt = str(value).strip()
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(txt, fmt)
            except ValueError:
                continue
        return None

    def _add_event(dt_raw: str | None, text: str):
        events.append((_parse_dt(dt_raw), text))

    with _db_connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        verleih = conn.execute(
            """
            SELECT von_datum, rueckgabe_datum, status, entleiher, return_comment, created_at, returned_at
            FROM verleih_planung
            WHERE item_type = ? AND item_id = ?
            ORDER BY created_at DESC
            """,
            (item_type, item_id),
        ).fetchall()
        for v in verleih:
            _add_event(
                v["created_at"],
                f"Verleih angelegt {v['von_datum']} bis {v['rueckgabe_datum']} | Status: {v['status']} | Entleiher: {v['entleiher'] or '-'}"
            )
            if v["returned_at"]:
                _add_event(
                    v["returned_at"],
                    f"Rückgabe bestätigt | Rückgabe-Kommentar: {v['return_comment'] or '-'}"
                )

        lock = conn.execute(
            """
            SELECT is_locked, lock_comment, unlock_comment, lock_at, unlock_at
            FROM item_locks
            WHERE item_type = ? AND item_id = ?
            """,
            (item_type, item_id),
        ).fetchone()
        if lock:
            if lock["lock_at"]:
                _add_event(lock["lock_at"], f"Gesperrt | Kommentar: {lock['lock_comment'] or '-'}")
            if lock["unlock_at"]:
                _add_event(lock["unlock_at"], f"Entsperrt | Kommentar: {lock['unlock_comment'] or '-'}")

        reports = conn.execute(
            """
            SELECT b.created_at, i.kommentar, i.naechste_pruefung_am, i.aktion
            FROM psa_pruefungsbericht_items i
            JOIN psa_pruefungsberichte b ON b.id = i.report_id
            WHERE i.item_type = ? AND i.item_id = ?
            ORDER BY b.created_at DESC
            """,
            (item_type, item_id),
        ).fetchall()
        for r in reports:
            _add_event(
                r["created_at"],
                f"PSA-Prüfung | Aktion: {r['aktion'] or '-'} | Nächste Prüfung: {r['naechste_pruefung_am'] or '-'} | Kommentar: {r['kommentar'] or '-'}"
            )

        if item_type == "product":
            fail_row = conn.execute(
                "SELECT COALESCE(last_psa_durchgefallen_am, '') FROM products WHERE id = ?",
                (item_id,),
            ).fetchone()
        else:
            fail_row = conn.execute(
                "SELECT COALESCE(last_psa_durchgefallen_am, '') FROM systems WHERE id = ?",
                (item_id,),
            ).fetchone()
        fail_ts = str(fail_row[0] or "").strip() if fail_row else ""
        if fail_ts:
            _add_event(fail_ts, "PSA-Prüfung: Durchgefallen")

    events.sort(key=lambda t: (t[0] is None, t[0] if t[0] else datetime.min), reverse=True)
    lines: list[str] = []
    for dt, text in events:
        prefix = dt.strftime("%Y-%m-%d %H:%M:%S") if dt else "ohne Datum"
        lines.append(f"{prefix} | {text}")
    return lines


def find_einzelidentifikation_conflict(db_path: Path, einzelidentifikation: str) -> str | None:
    ei = (einzelidentifikation or "").strip()
    if not ei:
        return None

    with _db_connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT id
            FROM products
            WHERE LOWER(TRIM(COALESCE(einzelidentifikation, ''))) = LOWER(TRIM(?))
            LIMIT 1
            """,
            (ei,),
        ).fetchone()
        if row:
            return f"bereits bei Produkt #{int(row[0])} vorhanden"

        row = conn.execute(
            """
            SELECT system_id, part_index
            FROM system_parts
            WHERE LOWER(TRIM(COALESCE(einzelidentifikation, ''))) = LOWER(TRIM(?))
            LIMIT 1
            """,
            (ei,),
        ).fetchone()
        if row:
            return f"bereits bei System #{int(row[0])}, Teil {int(row[1])} vorhanden"

    return None


def has_active_loans(db_path: Path, *, item_type: str, item_id: int) -> bool:
    with _db_connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT 1
            FROM verleih_planung
            WHERE item_type = ?
              AND item_id = ?
              AND COALESCE(status, '') IN ('reserved', 'lent')
            LIMIT 1
            """,
            (item_type, item_id),
        ).fetchone()
    return row is not None


def delete_item(db_path: Path, *, item_type: str, item_id: int) -> None:
    with _db_connect(db_path) as conn:
        if item_type == 'product':
            conn.execute("DELETE FROM products WHERE id = ?", (item_id,))
        else:
            conn.execute("DELETE FROM system_parts WHERE system_id = ?", (item_id,))
            conn.execute("DELETE FROM systems WHERE id = ?", (item_id,))
        conn.execute("DELETE FROM item_locks WHERE item_type = ? AND item_id = ?", (item_type, item_id))


def create_psa_report(db_path: Path, *, signer_name: str, signature_data: str, items: list[dict]) -> int:
    with _db_connect(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO psa_pruefungsberichte (created_at, sachkundiger_name, sachkundig_bestaetigt, signature_data)
            VALUES (?, ?, 1, ?)
            """,
            (datetime.now().isoformat(timespec='seconds'), signer_name, signature_data),
        )
        report_id = int(cur.lastrowid)

        for item in items:
            conn.execute(
                """
                INSERT INTO psa_pruefungsbericht_items (
                    report_id, item_type, item_id, item_label,
                    psa_pruefung_durchgefuehrt, kommentar, naechste_pruefung_am, aktion
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    report_id,
                    item.get('item_type', ''),
                    item.get('item_id'),
                    item.get('item_label', ''),
                    1 if item.get('psa_done') else 0,
                    item.get('kommentar', ''),
                    item.get('naechste_pruefung_am', ''),
                    item.get('aktion', 'update'),
                ),
            )
    return report_id


def fetch_reports(db_path: Path) -> list[dict]:
    with _db_connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT b.id, b.created_at, b.sachkundiger_name,
                   (SELECT COUNT(*) FROM psa_pruefungsbericht_items i WHERE i.report_id = b.id) AS item_count
            FROM psa_pruefungsberichte b
            ORDER BY b.id DESC
            """
        ).fetchall()
    return [dict(r) for r in rows]


def fetch_report_details(db_path: Path, report_id: int) -> dict | None:
    with _db_connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        header = conn.execute("SELECT * FROM psa_pruefungsberichte WHERE id = ?", (report_id,)).fetchone()
        if not header:
            return None
        items = conn.execute(
            """
            SELECT *
            FROM psa_pruefungsbericht_items
            WHERE report_id = ?
            ORDER BY id ASC
            """,
            (report_id,),
        ).fetchall()
    return {"header": dict(header), "items": [dict(i) for i in items]}


class ProductEditDialog(tk.Toplevel):
    def __init__(self, master: tk.Misc, product: dict):
        super().__init__(master)
        self.title(f"Produkt bearbeiten #{product.get('id')}")
        self.geometry("640x560")
        self.transient(master)
        self.grab_set()

        self.result: dict | None = None

        frame = ttk.Frame(self)
        frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        frame.columnconfigure(1, weight=1)

        self.entries: dict[str, ttk.Entry] = {}
        for i, (label, key) in enumerate(PRODUCT_FIELDS):
            ttk.Label(frame, text=label).grid(row=i, column=0, sticky="w", padx=4, pady=2)
            e = ttk.Entry(frame)
            e.grid(row=i, column=1, sticky="ew", padx=4, pady=2)
            e.insert(0, str(product.get(key, "") or ""))
            self.entries[key] = e

        self.gal_file_var = tk.StringVar(value=str(product.get("gal_datei", "") or ""))
        ttk.Label(frame, text="GAL-Datei").grid(row=len(PRODUCT_FIELDS), column=0, sticky="w", padx=4, pady=2)
        gal_row = ttk.Frame(frame)
        gal_row.grid(row=len(PRODUCT_FIELDS), column=1, sticky="ew", padx=4, pady=2)
        gal_row.columnconfigure(0, weight=1)
        ttk.Label(gal_row, textvariable=self.gal_file_var, relief=tk.SUNKEN).grid(row=0, column=0, sticky="ew")
        ttk.Button(gal_row, text="Datei wählen", command=self._browse_file).grid(row=0, column=1, padx=(6, 0))

        ttk.Label(frame, text="GAL-Link").grid(row=len(PRODUCT_FIELDS) + 1, column=0, sticky="w", padx=4, pady=2)
        self.gal_link_entry = ttk.Entry(frame)
        self.gal_link_entry.grid(row=len(PRODUCT_FIELDS) + 1, column=1, sticky="ew", padx=4, pady=2)
        self.gal_link_entry.insert(0, str(product.get("gal_link", "") or ""))

        ttk.Label(frame, text="Nächste Prüfung am").grid(row=len(PRODUCT_FIELDS) + 2, column=0, sticky="w", padx=4, pady=2)
        next_row = ttk.Frame(frame)
        next_row.grid(row=len(PRODUCT_FIELDS) + 2, column=1, sticky="ew", padx=4, pady=2)
        next_row.columnconfigure(0, weight=1)
        self.next_check_entry = ttk.Entry(next_row)
        self.next_check_entry.grid(row=0, column=0, sticky="ew")
        self.next_check_entry.insert(0, str(product.get("naechste_pruefung_am", "") or ""))
        ttk.Button(next_row, text="+6M", width=6, command=lambda: self._set_offset(6)).grid(row=0, column=1, padx=(4, 0))
        ttk.Button(next_row, text="+1J", width=6, command=lambda: self._set_offset(12)).grid(row=0, column=2, padx=(4, 0))

        btns = ttk.Frame(frame)
        btns.grid(row=len(PRODUCT_FIELDS) + 3, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Button(btns, text="Abbrechen", command=self._cancel).pack(side=tk.RIGHT)
        ttk.Button(btns, text="Änderungen übernehmen", command=self._save).pack(side=tk.RIGHT, padx=(0, 6))

    def _set_offset(self, months: int):
        self.next_check_entry.delete(0, tk.END)
        self.next_check_entry.insert(0, _add_months_iso(_today_iso(), months))

    def _browse_file(self):
        p = filedialog.askopenfilename(title="GAL-Datei auswählen", filetypes=[("PDF", "*.pdf"), ("Alle Dateien", "*.*")])
        if p:
            self.gal_file_var.set(p)

    def _save(self):
        data = {k: e.get().strip() for k, e in self.entries.items()}
        data["gal_datei"] = self.gal_file_var.get().strip()
        data["gal_link"] = self.gal_link_entry.get().strip()
        data["naechste_pruefung_am"] = self.next_check_entry.get().strip()
        self.result = data
        self.destroy()

    def _cancel(self):
        self.result = None
        self.destroy()


class SystemEditDialog(tk.Toplevel):
    def __init__(self, master: tk.Misc, system_data: dict, parts: dict[int, dict]):
        super().__init__(master)
        self.title(f"System bearbeiten #{system_data.get('id')}")
        self.geometry("980x760")
        self.transient(master)
        self.grab_set()

        self.result: dict | None = None
        wrapper = ttk.Frame(self)
        wrapper.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        wrapper.columnconfigure(0, weight=1)

        top = ttk.LabelFrame(wrapper, text="System-Stammdaten")
        top.grid(row=0, column=0, sticky="ew")
        top.columnconfigure(1, weight=1)

        ttk.Label(top, text="Systemname").grid(row=0, column=0, sticky="w", padx=6, pady=2)
        self.system_name_entry = ttk.Entry(top)
        self.system_name_entry.grid(row=0, column=1, sticky="ew", padx=6, pady=2)
        self.system_name_entry.insert(0, str(system_data.get("name", "") or ""))

        self.gal_file_var = tk.StringVar(value=str(system_data.get("gal_datei", "") or ""))
        ttk.Label(top, text="GAL-Datei").grid(row=1, column=0, sticky="w", padx=6, pady=2)
        gal_row = ttk.Frame(top)
        gal_row.grid(row=1, column=1, sticky="ew", padx=6, pady=2)
        gal_row.columnconfigure(0, weight=1)
        ttk.Label(gal_row, textvariable=self.gal_file_var, relief=tk.SUNKEN).grid(row=0, column=0, sticky="ew")
        ttk.Button(gal_row, text="Datei wählen", command=self._browse_file).grid(row=0, column=1, padx=(6, 0))

        ttk.Label(top, text="GAL-Link").grid(row=2, column=0, sticky="w", padx=6, pady=2)
        self.gal_link_entry = ttk.Entry(top)
        self.gal_link_entry.grid(row=2, column=1, sticky="ew", padx=6, pady=2)
        self.gal_link_entry.insert(0, str(system_data.get("gal_link", "") or ""))

        ttk.Label(top, text="Nächste Prüfung am").grid(row=3, column=0, sticky="w", padx=6, pady=2)
        next_row = ttk.Frame(top)
        next_row.grid(row=3, column=1, sticky="ew", padx=6, pady=2)
        next_row.columnconfigure(0, weight=1)
        self.next_check_entry = ttk.Entry(next_row)
        self.next_check_entry.grid(row=0, column=0, sticky="ew")
        self.next_check_entry.insert(0, str(system_data.get("naechste_pruefung_am", "") or ""))
        ttk.Button(next_row, text="+6M", width=6, command=lambda: self._set_offset(6)).grid(row=0, column=1, padx=(4, 0))
        ttk.Button(next_row, text="+1J", width=6, command=lambda: self._set_offset(12)).grid(row=0, column=2, padx=(4, 0))

        part_frame = ttk.LabelFrame(wrapper, text="Systemteile")
        part_frame.grid(row=1, column=0, sticky="nsew", pady=(8, 0))
        wrapper.rowconfigure(1, weight=1)
        part_frame.columnconfigure(0, weight=1)
        part_frame.columnconfigure(1, weight=1)
        part_frame.columnconfigure(2, weight=1)

        self.part_entries: dict[int, dict[str, ttk.Entry]] = {}
        for idx in (1, 2, 3):
            lf = ttk.LabelFrame(part_frame, text=f"Teil {idx}")
            lf.grid(row=0, column=idx - 1, sticky="nsew", padx=4, pady=4)
            lf.columnconfigure(1, weight=1)

            values = parts.get(idx, {})
            entries: dict[str, ttk.Entry] = {}
            for i, (label, key) in enumerate(PRODUCT_FIELDS):
                ttk.Label(lf, text=label).grid(row=i, column=0, sticky="w", padx=4, pady=2)
                e = ttk.Entry(lf)
                e.grid(row=i, column=1, sticky="ew", padx=4, pady=2)
                e.insert(0, str(values.get(key, "") or ""))
                entries[key] = e
            self.part_entries[idx] = entries

        btns = ttk.Frame(wrapper)
        btns.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        ttk.Button(btns, text="Abbrechen", command=self._cancel).pack(side=tk.RIGHT)
        ttk.Button(btns, text="Änderungen übernehmen", command=self._save).pack(side=tk.RIGHT, padx=(0, 6))

    def _browse_file(self):
        p = filedialog.askopenfilename(title="GAL-Datei auswählen", filetypes=[("PDF", "*.pdf"), ("Alle Dateien", "*.*")])
        if p:
            self.gal_file_var.set(p)

    def _set_offset(self, months: int):
        self.next_check_entry.delete(0, tk.END)
        self.next_check_entry.insert(0, _add_months_iso(_today_iso(), months))

    def _save(self):
        self.result = {
            "system": {
                "name": self.system_name_entry.get().strip(),
                "gal_datei": self.gal_file_var.get().strip(),
                "gal_link": self.gal_link_entry.get().strip(),
                "naechste_pruefung_am": self.next_check_entry.get().strip(),
            },
            "parts": {
                idx: {k: e.get().strip() for k, e in entries.items()}
                for idx, entries in self.part_entries.items()
            },
        }
        self.destroy()

    def _cancel(self):
        self.result = None
        self.destroy()


class ItemPruefungDialog(tk.Toplevel):
    def __init__(self, master: tk.Misc, *, item: dict, vorgaenge: list[str], initial: dict | None):
        super().__init__(master)
        self.title(f"Prüfung: {item.get('item_label', '')}")
        self.geometry("900x700")
        self.transient(master)
        self.grab_set()
        self.result: dict | None = None

        wrap = ttk.Frame(self)
        wrap.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        wrap.columnconfigure(0, weight=1)

        ttk.Label(wrap, text=item.get("item_label", "")).grid(row=0, column=0, sticky="w")
        ttk.Label(wrap, text=f"Nächste Prüfung: {item.get('naechste_pruefung_am') or '-'}").grid(row=1, column=0, sticky="w", pady=(0, 6))
        ttk.Label(wrap, text=f"Letzter Durchfall: {item.get('last_psa_durchgefallen_am') or '-'}").grid(row=2, column=0, sticky="w", pady=(0, 6))

        hist = tk.Text(wrap, height=16, wrap="word")
        hist.grid(row=3, column=0, sticky="nsew")
        wrap.rowconfigure(3, weight=1)
        hist.insert("1.0", "\n".join(vorgaenge) if vorgaenge else "Keine Vorgänge vorhanden.")
        hist.configure(state=tk.DISABLED)

        form = ttk.LabelFrame(wrap, text="PSA-Prüfung")
        form.grid(row=4, column=0, sticky="ew", pady=(8, 0))
        form.columnconfigure(1, weight=1)

        self.psa_done_var = tk.BooleanVar(value=bool((initial or {}).get("psa_done", False)))
        ttk.Checkbutton(form, text="PSA-Prüfung durchgeführt", variable=self.psa_done_var).grid(row=0, column=0, columnspan=2, sticky="w", padx=6, pady=2)

        ttk.Label(form, text="Kommentar").grid(row=1, column=0, sticky="nw", padx=6, pady=2)
        self.comment_text = tk.Text(form, height=4)
        self.comment_text.grid(row=1, column=1, sticky="ew", padx=6, pady=2)
        self.comment_text.insert("1.0", str((initial or {}).get("kommentar", "")))

        ttk.Label(form, text="Nächste Prüfung (YYYY-MM-DD)").grid(row=2, column=0, sticky="w", padx=6, pady=2)
        self.next_entry = ttk.Entry(form)
        self.next_entry.grid(row=2, column=1, sticky="ew", padx=6, pady=2)
        self.next_entry.insert(0, str((initial or {}).get("naechste_pruefung_am", item.get("naechste_pruefung_am", "")) or ""))

        btn_plus = ttk.Frame(form)
        btn_plus.grid(row=3, column=1, sticky="w", padx=6, pady=2)
        ttk.Button(btn_plus, text="+6 Monate", command=self._set_plus_6).pack(side=tk.LEFT)
        ttk.Button(btn_plus, text="+1 Jahr", command=self._set_plus_1y).pack(side=tk.LEFT, padx=(6, 0))

        self.delete_var = tk.BooleanVar(value=bool((initial or {}).get("delete", False)))
        ttk.Checkbutton(form, text="Produkt/System löschen", variable=self.delete_var).grid(row=4, column=0, columnspan=2, sticky="w", padx=6, pady=2)

        is_locked = int(item.get("is_locked", 0)) == 1
        self.lock_var = tk.BooleanVar(value=bool((initial or {}).get("lock", False)))
        lock_cb = ttk.Checkbutton(form, text="Produkt/System sperren", variable=self.lock_var)
        lock_cb.grid(row=5, column=0, columnspan=2, sticky="w", padx=6, pady=2)
        if is_locked:
            self.lock_var.set(False)
            lock_cb.configure(state=tk.DISABLED)

        ttk.Label(form, text="Sperr-Kommentar").grid(row=6, column=0, sticky="w", padx=6, pady=2)
        self.lock_comment_entry = ttk.Entry(form)
        self.lock_comment_entry.grid(row=6, column=1, sticky="ew", padx=6, pady=2)
        self.lock_comment_entry.insert(0, str((initial or {}).get("lock_comment", "")))
        if is_locked:
            self.lock_comment_entry.configure(state=tk.DISABLED)

        self.unlock_var = tk.BooleanVar(value=bool((initial or {}).get("unlock", False)))
        unlock_cb = ttk.Checkbutton(form, text="Produkt/System entsperren", variable=self.unlock_var)
        unlock_cb.grid(row=7, column=0, columnspan=2, sticky="w", padx=6, pady=2)
        if not is_locked:
            self.unlock_var.set(False)
            unlock_cb.configure(state=tk.DISABLED)

        ttk.Label(form, text="Entsperr-Kommentar").grid(row=8, column=0, sticky="w", padx=6, pady=2)
        self.unlock_comment_entry = ttk.Entry(form)
        self.unlock_comment_entry.grid(row=8, column=1, sticky="ew", padx=6, pady=2)
        self.unlock_comment_entry.insert(0, str((initial or {}).get("unlock_comment", "")))
        if not is_locked:
            self.unlock_comment_entry.configure(state=tk.DISABLED)

        actions = ttk.Frame(wrap)
        actions.grid(row=5, column=0, sticky="ew", pady=(8, 0))
        ttk.Button(actions, text="Haftungsausschluss erzeugen", command=self._gen_haftung).pack(side=tk.LEFT)
        ttk.Button(actions, text="Abbrechen", command=self._cancel).pack(side=tk.RIGHT)
        ttk.Button(actions, text="In Prüfungsrunde übernehmen", command=self._save).pack(side=tk.RIGHT, padx=(0, 6))

        self._item = item

    def _set_plus_6(self):
        self.next_entry.delete(0, tk.END)
        self.next_entry.insert(0, _add_months_iso(_today_iso(), 6))

    def _set_plus_1y(self):
        self.next_entry.delete(0, tk.END)
        self.next_entry.insert(0, _add_months_iso(_today_iso(), 12))

    def _gen_haftung(self):
        item_type = self._item.get("item_type")
        item_id = int(self._item.get("item_id"))
        try:
            if item_type == "product":
                generate_haftungsausschluss_pdf_from_db(product_id=item_id, system_id=None)
            else:
                generate_haftungsausschluss_pdf_from_db(product_id=None, system_id=item_id)
            messagebox.showinfo("OK", "Haftungsausschluss wurde erzeugt.", parent=self)
        except Exception as e:
            messagebox.showerror("Fehler", f"Konnte Haftungsausschluss nicht erzeugen: {e}", parent=self)

    def _save(self):
        next_date = self.next_entry.get().strip()
        if next_date:
            try:
                datetime.strptime(next_date, "%Y-%m-%d")
            except ValueError:
                messagebox.showerror("Fehler", "Datum bitte im Format YYYY-MM-DD eingeben.", parent=self)
                return
        self.result = {
            "psa_done": bool(self.psa_done_var.get()),
            "kommentar": self.comment_text.get("1.0", tk.END).strip(),
            "naechste_pruefung_am": next_date,
            "delete": bool(self.delete_var.get()),
            "lock": bool(self.lock_var.get()),
            "lock_comment": self.lock_comment_entry.get().strip(),
            "unlock": bool(self.unlock_var.get()),
            "unlock_comment": self.unlock_comment_entry.get().strip(),
        }

        if self.result["lock"] and self.result["unlock"]:
            messagebox.showerror("Fehler", "Sperren und Entsperren gleichzeitig ist nicht möglich.", parent=self)
            return
        if self.result["lock"] and not self.result["lock_comment"]:
            messagebox.showerror("Fehler", "Zum Sperren ist ein Kommentar erforderlich.", parent=self)
            return

        if self.result["unlock"] and not self.result["unlock_comment"]:
            messagebox.showerror("Fehler", "Zum Entsperren ist ein Kommentar erforderlich.", parent=self)
            return
        self.destroy()

    def _cancel(self):
        self.result = None
        self.destroy()


class SachkundigSignDialog(tk.Toplevel):
    def __init__(self, master: tk.Misc):
        super().__init__(master)
        self.title("PSA-Sachkundigen-Bestätigung")
        self.geometry("640x430")
        self.transient(master)
        self.grab_set()
        self.result: dict | None = None

        ttk.Label(self, text="Name PSA-Sachkundiger").pack(anchor="w", padx=8, pady=(8, 2))
        self.name_entry = ttk.Entry(self)
        self.name_entry.pack(fill="x", padx=8, pady=(0, 6))

        self.confirm_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(self, text="Ich bin PSA-Sachkundig", variable=self.confirm_var).pack(anchor="w", padx=8, pady=(0, 6))

        ttk.Label(self, text="Unterschrift").pack(anchor="w", padx=8, pady=(0, 2))
        self.canvas = tk.Canvas(self, width=620, height=260, bg="white", highlightthickness=1, highlightbackground="#888")
        self.canvas.pack(padx=8, pady=6)

        self._strokes: list[list[tuple[int, int]]] = []
        self._current: list[tuple[int, int]] = []
        self.canvas.bind("<ButtonPress-1>", self._start)
        self.canvas.bind("<B1-Motion>", self._draw)
        self.canvas.bind("<ButtonRelease-1>", self._end)

        b = ttk.Frame(self)
        b.pack(fill="x", padx=8, pady=8)
        ttk.Button(b, text="Leeren", command=self._clear).pack(side=tk.LEFT)
        ttk.Button(b, text="Abbrechen", command=self._cancel).pack(side=tk.RIGHT)
        ttk.Button(b, text="Bestätigen", command=self._save).pack(side=tk.RIGHT, padx=(0, 6))

    def _start(self, e):
        self._current = [(e.x, e.y)]

    def _draw(self, e):
        if not self._current:
            self._current = [(e.x, e.y)]
            return
        x, y = self._current[-1]
        self.canvas.create_line(x, y, e.x, e.y, width=2, fill="black", smooth=True)
        self._current.append((e.x, e.y))

    def _end(self, _):
        if len(self._current) > 1:
            self._strokes.append(self._current[:])
        self._current = []

    def _clear(self):
        self.canvas.delete("all")
        self._strokes.clear()
        self._current = []

    def _save(self):
        if not self.name_entry.get().strip():
            messagebox.showerror("Fehler", "Bitte Namen eingeben.", parent=self)
            return
        if not self.confirm_var.get():
            messagebox.showerror("Fehler", "Bitte PSA-Sachkundig bestätigen.", parent=self)
            return
        if not self._strokes:
            messagebox.showerror("Fehler", "Bitte unterschreiben.", parent=self)
            return
        self.result = {
            "name": self.name_entry.get().strip(),
            "signature": json.dumps(self._strokes),
        }
        self.destroy()

    def _cancel(self):
        self.result = None
        self.destroy()


class ReportDetailsDialog(tk.Toplevel):
    def __init__(self, master: tk.Misc, data: dict):
        super().__init__(master)
        self.title(f"Prüfungsbericht #{data['header']['id']}")
        self.geometry("900x700")
        self.transient(master)

        wrap = ttk.Frame(self)
        wrap.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        wrap.columnconfigure(0, weight=1)

        h = data["header"]
        ttk.Label(wrap, text=f"Datum: {h.get('created_at','')}").grid(row=0, column=0, sticky='w')
        ttk.Label(wrap, text=f"Sachkundiger: {h.get('sachkundiger_name','')}").grid(row=1, column=0, sticky='w', pady=(0,6))

        tree = ttk.Treeview(wrap, columns=("item", "done", "next", "comment", "action"), show="headings", height=12)
        tree.heading("item", text="Produkt/System")
        tree.heading("done", text="PSA-Prüfung")
        tree.heading("next", text="Nächste Prüfung")
        tree.heading("comment", text="Kommentar")
        tree.heading("action", text="Aktion")
        tree.column("item", width=260)
        tree.column("done", width=110, anchor='center')
        tree.column("next", width=120, anchor='center')
        tree.column("comment", width=300)
        tree.column("action", width=90, anchor='center')
        tree.grid(row=2, column=0, sticky='nsew')
        wrap.rowconfigure(2, weight=1)

        for i in data["items"]:
            tree.insert(
                "",
                tk.END,
                values=(
                    i.get("item_label", ""),
                    "Ja" if int(i.get("psa_pruefung_durchgefuehrt") or 0) else "Nein",
                    i.get("naechste_pruefung_am", ""),
                    i.get("kommentar", ""),
                    i.get("aktion", ""),
                ),
            )

        ttk.Label(wrap, text="Unterschrift").grid(row=3, column=0, sticky='w', pady=(8,2))
        cv = tk.Canvas(wrap, width=860, height=220, bg='white', highlightthickness=1, highlightbackground='#888')
        cv.grid(row=4, column=0, sticky='ew')
        try:
            strokes = json.loads(h.get("signature_data", "") or "[]")
            for stroke in strokes:
                if not isinstance(stroke, list) or len(stroke) < 2:
                    continue
                for idx in range(1, len(stroke)):
                    x1, y1 = stroke[idx-1]
                    x2, y2 = stroke[idx]
                    cv.create_line(x1, y1, x2, y2, width=2, fill='black', smooth=True)
        except Exception:
            cv.create_text(10, 10, anchor='nw', text='Unterschrift konnte nicht geladen werden', fill='#b00020')

        ttk.Button(wrap, text="Schließen", command=self.destroy).grid(row=5, column=0, sticky='e', pady=(8,0))


class ArchivedItemDetailsDialog(tk.Toplevel):
    def __init__(self, master: tk.Misc, *, archived_item: dict, details: dict | None, vorgaenge: list[str]):
        super().__init__(master)
        self.title("Archivdetails")
        self.geometry("980x760")
        self.transient(master)

        wrap = ttk.Frame(self)
        wrap.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        wrap.columnconfigure(0, weight=1)

        ttk.Label(wrap, text=archived_item.get("item_label", "")).grid(row=0, column=0, sticky="w")
        ttk.Label(
            wrap,
            text=f"Archiviert am: {archived_item.get('archived_at', '')} | Von: {archived_item.get('archived_by', '') or '-'}",
        ).grid(row=1, column=0, sticky="w", pady=(0, 6))
        ttk.Label(wrap, text=f"Kommentar: {archived_item.get('kommentar', '') or '-'}").grid(row=2, column=0, sticky="w", pady=(0, 6))

        info_frame = ttk.LabelFrame(wrap, text="Stammdaten")
        info_frame.grid(row=3, column=0, sticky="nsew")
        info_frame.columnconfigure(0, weight=1)

        info_text = tk.Text(info_frame, height=10, wrap="word")
        info_text.grid(row=0, column=0, sticky="nsew", padx=6, pady=6)
        info_frame.rowconfigure(0, weight=1)

        if details:
            lines: list[str] = []
            if "system" in details:
                lines.append("System:")
                for k, v in details.get("system", {}).items():
                    lines.append(f"- {k}: {v}")
                parts = details.get("parts", {})
                if parts:
                    lines.append("")
                    lines.append("Systemteile:")
                    for idx in sorted(parts.keys()):
                        lines.append(f"Teil {idx}:")
                        for k, v in parts[idx].items():
                            if k == "part_index":
                                continue
                            lines.append(f"  - {k}: {v}")
            else:
                lines.append("Produkt:")
                for k, v in details.items():
                    lines.append(f"- {k}: {v}")
            info_text.insert("1.0", "\n".join(lines))
        else:
            info_text.insert("1.0", "Keine Stammdaten gefunden.")
        info_text.configure(state=tk.DISABLED)

        hist_frame = ttk.LabelFrame(wrap, text="Vorgänge")
        hist_frame.grid(row=4, column=0, sticky="nsew", pady=(8, 0))
        hist_frame.columnconfigure(0, weight=1)
        hist_frame.rowconfigure(0, weight=1)
        wrap.rowconfigure(4, weight=1)

        hist_text = tk.Text(hist_frame, height=16, wrap="word")
        hist_text.grid(row=0, column=0, sticky="nsew", padx=6, pady=6)
        hist_text.insert("1.0", "\n".join(vorgaenge) if vorgaenge else "Keine Vorgänge vorhanden.")
        hist_text.configure(state=tk.DISABLED)

        ttk.Button(wrap, text="Schließen", command=self.destroy).grid(row=5, column=0, sticky="e", pady=(8, 0))


class PSAApp(ttk.Frame):
    def __init__(self, master: tk.Tk, db_path: Path):
        super().__init__(master)
        self.db_path = db_path
        self.pack(fill=tk.BOTH, expand=True)
        self.pending_pruefungen: dict[tuple[str, int], dict] = {}

        self._build_ui()
        self.refresh_lists()
        self.refresh_due_list()
        self.refresh_reports_list()
        self.refresh_archive_list()

    def _build_ui(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        notebook = ttk.Notebook(self)
        notebook.grid(row=0, column=0, sticky="nsew")

        main_tab = ttk.Frame(notebook)
        due_tab = ttk.Frame(notebook)
        report_tab = ttk.Frame(notebook)
        archive_tab = ttk.Frame(notebook)
        notebook.add(main_tab, text="Stammdaten & PDF")
        notebook.add(due_tab, text="Nächste Prüfungen")
        notebook.add(report_tab, text="PSA-Prüfungsberichte")
        notebook.add(archive_tab, text="Archiv (Aussortiert)")

        main_tab.columnconfigure(0, weight=1)
        main_tab.columnconfigure(1, weight=1)

        product_frame = ttk.LabelFrame(main_tab, text="Produkt anlegen")
        product_frame.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)

        system_frame = ttk.LabelFrame(main_tab, text="System anlegen")
        system_frame.grid(row=0, column=1, sticky="nsew", padx=8, pady=8)

        select_frame = ttk.LabelFrame(main_tab, text="Auswahl & PDF-Erstellung")
        select_frame.grid(row=1, column=0, columnspan=2, sticky="nsew", padx=8, pady=8)

        self._build_product_form(product_frame)
        self._build_system_form(system_frame)
        self._build_select_area(select_frame)
        self._build_due_tab(due_tab)
        self._build_report_tab(report_tab)
        self._build_archive_tab(archive_tab)

    def _build_due_tab(self, parent: ttk.Frame):
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)

        self.due_tree = ttk.Treeview(
            parent,
            columns=("label", "next", "locked", "last"),
            show="headings",
            height=18,
        )
        self.due_tree.heading("label", text="Bezeichnung")
        self.due_tree.heading("next", text="Nächste Prüfung")
        self.due_tree.heading("locked", text="Gesperrt")
        self.due_tree.heading("last", text="Letzte PSA-Prüfung")
        self.due_tree.column("label", width=450, anchor="w")
        self.due_tree.column("next", width=130, anchor="center")
        self.due_tree.column("locked", width=100, anchor="center")
        self.due_tree.column("last", width=150, anchor="center")
        self.due_tree.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        self.due_tree.bind("<Double-1>", self._open_due_item)
        self.due_tree.tag_configure("pending", background="#fff3cd")
        self.due_tree.tag_configure("locked", background="#f8d7da")

        actions = ttk.Frame(parent)
        actions.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 8))
        ttk.Button(actions, text="Liste aktualisieren", command=self.refresh_due_list).pack(side=tk.LEFT)
        ttk.Button(actions, text="Prüfungsrunde bestätigen", command=self.commit_pending_pruefungen).pack(side=tk.RIGHT)

    def _build_report_tab(self, parent: ttk.Frame):
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)

        self.report_tree = ttk.Treeview(
            parent,
            columns=("id", "date", "name", "count"),
            show="headings",
            height=18,
        )
        self.report_tree.heading("id", text="ID")
        self.report_tree.heading("date", text="Datum")
        self.report_tree.heading("name", text="Sachkundiger")
        self.report_tree.heading("count", text="Anzahl Objekte")
        self.report_tree.column("id", width=70, anchor="center")
        self.report_tree.column("date", width=180, anchor="center")
        self.report_tree.column("name", width=240, anchor="w")
        self.report_tree.column("count", width=130, anchor="center")
        self.report_tree.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        self.report_tree.bind("<Double-1>", self._open_report_details)

        actions = ttk.Frame(parent)
        actions.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 8))
        ttk.Button(actions, text="Berichte aktualisieren", command=self.refresh_reports_list).pack(side=tk.LEFT)

    def _build_archive_tab(self, parent: ttk.Frame):
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)

        self.archive_tree = ttk.Treeview(
            parent,
            columns=("label", "date", "by", "comment"),
            show="headings",
            height=18,
        )
        self.archive_tree.heading("label", text="Bezeichnung")
        self.archive_tree.heading("date", text="Archiviert am")
        self.archive_tree.heading("by", text="Archiviert von")
        self.archive_tree.heading("comment", text="Kommentar")
        self.archive_tree.column("label", width=360, anchor="w")
        self.archive_tree.column("date", width=160, anchor="center")
        self.archive_tree.column("by", width=160, anchor="w")
        self.archive_tree.column("comment", width=340, anchor="w")
        self.archive_tree.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        self.archive_tree.bind("<Double-1>", self._open_archive_item)

        actions = ttk.Frame(parent)
        actions.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 8))
        ttk.Button(actions, text="Archiv aktualisieren", command=self.refresh_archive_list).pack(side=tk.LEFT)
        ttk.Button(actions, text="Haftungsausschluss erzeugen", command=self.generate_archive_haftung).pack(side=tk.RIGHT)

    def _build_product_form(self, parent: ttk.LabelFrame):
        self.product_entries = {}
        for i, (label, key) in enumerate(PRODUCT_FIELDS):
            ttk.Label(parent, text=label).grid(row=i, column=0, sticky="w", padx=6, pady=2)
            entry = ttk.Entry(parent, width=30)
            entry.grid(row=i, column=1, sticky="ew", padx=6, pady=2)
            self.product_entries[key] = entry

        next_row = len(PRODUCT_FIELDS)
        ttk.Label(parent, text="Nächste Prüfung am").grid(row=next_row, column=0, sticky="w", padx=6, pady=2)
        next_frame = ttk.Frame(parent)
        next_frame.grid(row=next_row, column=1, sticky="ew", padx=6, pady=2)
        next_frame.columnconfigure(0, weight=1)
        self.product_next_check_entry = ttk.Entry(next_frame)
        self.product_next_check_entry.grid(row=0, column=0, sticky="ew")
        ttk.Button(next_frame, text="+6M", width=6, command=lambda: self._fill_date_offset(self.product_next_check_entry, 6)).grid(row=0, column=1, padx=(4, 0))
        ttk.Button(next_frame, text="+1J", width=6, command=lambda: self._fill_date_offset(self.product_next_check_entry, 12)).grid(row=0, column=2, padx=(4, 0))
        parent.columnconfigure(1, weight=1)

        # GAL-Bereich
        ttk.Label(parent, text="GAL (optional)").grid(row=len(PRODUCT_FIELDS)+1, column=0, columnspan=2, sticky="w", padx=6, pady=(8, 2))
        
        self.product_gal_file_var = tk.StringVar(value="")
        ttk.Label(parent, text="GAL-Datei:").grid(row=len(PRODUCT_FIELDS)+2, column=0, sticky="w", padx=6, pady=2)
        file_btn_frame = ttk.Frame(parent)
        file_btn_frame.grid(row=len(PRODUCT_FIELDS)+2, column=1, sticky="ew", padx=6, pady=2)
        file_btn_frame.columnconfigure(0, weight=1)
        ttk.Button(file_btn_frame, text="Datei wählen", command=self._browse_product_gal).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Label(file_btn_frame, textvariable=self.product_gal_file_var, relief=tk.SUNKEN).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(6, 0))

        ttk.Label(parent, text="GAL-Link:").grid(row=len(PRODUCT_FIELDS)+3, column=0, sticky="w", padx=6, pady=2)
        self.product_gal_link_entry = ttk.Entry(parent, width=30)
        self.product_gal_link_entry.grid(row=len(PRODUCT_FIELDS)+3, column=1, sticky="ew", padx=6, pady=2)

        ttk.Button(parent, text="Produkt speichern", command=self.save_product).grid(
            row=len(PRODUCT_FIELDS)+4, column=0, columnspan=2, sticky="ew", padx=6, pady=6
        )

    def _build_system_form(self, parent: ttk.LabelFrame):
        ttk.Label(parent, text="Systemname").grid(row=0, column=0, sticky="w", padx=6, pady=2)
        self.system_name_entry = ttk.Entry(parent, width=30)
        self.system_name_entry.grid(row=0, column=1, sticky="ew", padx=6, pady=2)

        ttk.Label(parent, text="Nächste Prüfung am").grid(row=1, column=0, sticky="w", padx=6, pady=2)
        next_frame = ttk.Frame(parent)
        next_frame.grid(row=1, column=1, sticky="ew", padx=6, pady=2)
        next_frame.columnconfigure(0, weight=1)
        self.system_next_check_entry = ttk.Entry(next_frame)
        self.system_next_check_entry.grid(row=0, column=0, sticky="ew")
        ttk.Button(next_frame, text="+6M", width=6, command=lambda: self._fill_date_offset(self.system_next_check_entry, 6)).grid(row=0, column=1, padx=(4, 0))
        ttk.Button(next_frame, text="+1J", width=6, command=lambda: self._fill_date_offset(self.system_next_check_entry, 12)).grid(row=0, column=2, padx=(4, 0))

        # GAL-Bereich
        ttk.Label(parent, text="GAL (optional)").grid(row=2, column=0, columnspan=2, sticky="w", padx=6, pady=(8, 2))
        
        self.system_gal_file_var = tk.StringVar(value="")
        ttk.Label(parent, text="GAL-Datei:").grid(row=3, column=0, sticky="w", padx=6, pady=2)
        file_btn_frame = ttk.Frame(parent)
        file_btn_frame.grid(row=3, column=1, sticky="ew", padx=6, pady=2)
        file_btn_frame.columnconfigure(0, weight=1)
        ttk.Button(file_btn_frame, text="Datei wählen", command=self._browse_system_gal).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Label(file_btn_frame, textvariable=self.system_gal_file_var, relief=tk.SUNKEN).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(6, 0))

        ttk.Label(parent, text="GAL-Link:").grid(row=4, column=0, sticky="w", padx=6, pady=2)
        self.system_gal_link_entry = ttk.Entry(parent, width=30)
        self.system_gal_link_entry.grid(row=4, column=1, sticky="ew", padx=6, pady=2)

        part_frame = ttk.Frame(parent)
        part_frame.grid(row=5, column=0, columnspan=2, sticky="nsew", padx=6, pady=6)
        part_frame.columnconfigure(0, weight=1)
        part_frame.columnconfigure(1, weight=1)

        # Teil 2 und 3 (Teil 1 kommt aus dem Produktformular)
        self.system_part2 = self._build_part_form(part_frame, "Teil 2", 0)
        self.system_part3 = self._build_part_form(part_frame, "Teil 3", 1)

        ttk.Button(parent, text="System speichern", command=self.save_system).grid(
            row=6, column=0, columnspan=2, sticky="ew", padx=6, pady=6
        )

    def _build_part_form(self, parent: ttk.Frame, title: str, column: int) -> dict:
        frame = ttk.LabelFrame(parent, text=title)
        frame.grid(row=0, column=column, sticky="nsew", padx=6, pady=6)
        entries = {}
        for i, (label, key) in enumerate(PRODUCT_FIELDS):
            ttk.Label(frame, text=label).grid(row=i, column=0, sticky="w", padx=6, pady=2)
            entry = ttk.Entry(frame, width=25)
            entry.grid(row=i, column=1, sticky="ew", padx=6, pady=2)
            entries[key] = entry
        frame.columnconfigure(1, weight=1)
        return entries

    def _build_select_area(self, parent: ttk.LabelFrame):
        parent.columnconfigure(0, weight=1)
        parent.columnconfigure(1, weight=1)

        ttk.Label(parent, text="Produkte").grid(row=0, column=0, sticky="w", padx=6, pady=2)
        ttk.Label(parent, text="Systeme").grid(row=0, column=1, sticky="w", padx=6, pady=2)

        self.product_list = tk.Listbox(parent, height=6)
        self.product_list.grid(row=1, column=0, sticky="nsew", padx=6, pady=2)
        self.product_list.bind("<Double-1>", self._on_product_double_click)

        self.system_list = tk.Listbox(parent, height=6)
        self.system_list.grid(row=1, column=1, sticky="nsew", padx=6, pady=2)
        self.system_list.bind("<Double-1>", self._on_system_double_click)

        options_frame = ttk.Frame(parent)
        options_frame.grid(row=2, column=0, columnspan=2, sticky="ew", padx=6, pady=6)
        options_frame.columnconfigure(0, weight=1)
        options_frame.columnconfigure(1, weight=1)
        options_frame.columnconfigure(2, weight=1)

        self.make_einsatz = tk.BooleanVar(value=True)
        self.make_pd = tk.BooleanVar(value=True)
        self.make_haftung = tk.BooleanVar(value=True)

        ttk.Checkbutton(options_frame, text="Einsatzdokumentation", variable=self.make_einsatz).grid(
            row=0, column=0, sticky="w"
        )
        ttk.Checkbutton(options_frame, text="PD", variable=self.make_pd).grid(
            row=0, column=1, sticky="w"
        )
        ttk.Checkbutton(options_frame, text="Haftungsausschluss", variable=self.make_haftung).grid(
            row=0, column=2, sticky="w"
        )

        button_frame = ttk.Frame(parent)
        button_frame.grid(row=3, column=0, columnspan=2, sticky="ew", padx=6, pady=6)
        button_frame.columnconfigure(0, weight=1)
        button_frame.columnconfigure(1, weight=1)

        ttk.Button(button_frame, text="Listen aktualisieren", command=self.refresh_lists).grid(
            row=0, column=0, sticky="ew", padx=4
        )
        ttk.Button(button_frame, text="PDFs erstellen", command=self.generate_selected_pdfs).grid(
            row=0, column=1, sticky="ew", padx=4
        )

        self.status_var = tk.StringVar(value="Bereit")
        ttk.Label(parent, textvariable=self.status_var).grid(
            row=4, column=0, columnspan=2, sticky="w", padx=6, pady=4
        )

    def _collect_entries(self, entries: dict) -> dict:
        return {key: entry.get().strip() for key, entry in entries.items()}

    def _fill_date_offset(self, entry: ttk.Entry, months: int):
        entry.delete(0, tk.END)
        entry.insert(0, _add_months_iso(_today_iso(), months))

    def _validate_date_or_empty(self, value: str) -> bool:
        if not value:
            return True
        try:
            datetime.strptime(value, "%Y-%m-%d")
            return True
        except ValueError:
            return False

    def _browse_product_gal(self):
        file_path = filedialog.askopenfilename(
            title="GAL-Datei auswählen",
            filetypes=[("PDF", "*.pdf"), ("Alle Dateien", "*.*")]
        )
        if file_path:
            self.product_gal_file_var.set(file_path)

    def _browse_system_gal(self):
        file_path = filedialog.askopenfilename(
            title="GAL-Datei auswählen",
            filetypes=[("PDF", "*.pdf"), ("Alle Dateien", "*.*")]
        )
        if file_path:
            self.system_gal_file_var.set(file_path)

    def _copy_gal_file(self, source_path: str, product_name: str) -> str:
        if not source_path:
            return ""
        try:
            source = Path(source_path)
            if not source.exists():
                return ""
            dest = GAL_DIR / f"{product_name}_{source.name}"
            shutil.copy2(source, dest)
            return str(dest)
        except Exception as e:
            messagebox.showerror("Fehler", f"Konnte GAL-Datei nicht kopieren: {e}")
            return ""

    def save_product(self):
        try:
            data = self._collect_entries(self.product_entries)
            ei = data.get("einzelidentifikation", "").strip()
            if not ei:
                messagebox.showerror("Fehler", "Für Produkte ist eine Einzelidentifikation erforderlich.")
                return
            conflict = find_einzelidentifikation_conflict(self.db_path, ei)
            if conflict:
                messagebox.showerror("Fehler", f"Einzelidentifikation '{ei}' ist nicht eindeutig ({conflict}).")
                return

            next_check = self.product_next_check_entry.get().strip()
            if not self._validate_date_or_empty(next_check):
                messagebox.showerror("Fehler", "Nächste Prüfung: Datum bitte im Format YYYY-MM-DD eingeben.")
                return
            gal_file = self.product_gal_file_var.get()
            gal_link = self.product_gal_link_entry.get().strip()
            
            # Datei kopieren falls vorhanden
            product_name = data.get("produktname", "produkt")
            if gal_file:
                gal_file = self._copy_gal_file(gal_file, product_name)
            
            data["gal_datei"] = gal_file
            data["gal_link"] = gal_link
            data["naechste_pruefung_am"] = next_check
            
            product_id = insert_product(self.db_path, data)
            self.status_var.set(f"Produkt gespeichert (ID {product_id})")
            
            # Felder löschen
            for entry in self.product_entries.values():
                entry.delete(0, tk.END)
            self.product_next_check_entry.delete(0, tk.END)
            self.product_gal_file_var.set("")
            self.product_gal_link_entry.delete(0, tk.END)
            
            self.refresh_lists()
        except Exception as e:
            messagebox.showerror("Fehler", f"Produkt konnte nicht gespeichert werden: {e}")

    def save_system(self):
        name = self.system_name_entry.get().strip() or "System"
        next_check = self.system_next_check_entry.get().strip()
        if not self._validate_date_or_empty(next_check):
            messagebox.showerror("Fehler", "Nächste Prüfung: Datum bitte im Format YYYY-MM-DD eingeben.")
            return

        # Teil 1 aus dem Produktformular (wird später als Systemteil 1 gespeichert)
        part1 = self._collect_entries(self.product_entries)
        part2 = self._collect_entries(self.system_part2)
        part3 = self._collect_entries(self.system_part3)

        system_eis: list[tuple[int, str]] = []
        for idx, part in ((1, part1), (2, part2), (3, part3)):
            ei = part.get("einzelidentifikation", "").strip()
            if ei:
                system_eis.append((idx, ei))

        if not system_eis:
            messagebox.showerror("Fehler", "Für das System muss mindestens eine Einzelidentifikation angegeben werden.")
            return

        for idx, ei in system_eis:
            conflict = find_einzelidentifikation_conflict(self.db_path, ei)
            if conflict:
                messagebox.showerror(
                    "Fehler",
                    f"Einzelidentifikation '{ei}' in Teil {idx} ist nicht eindeutig ({conflict}).",
                )
                return

        gal_file = self.system_gal_file_var.get()
        gal_link = self.system_gal_link_entry.get().strip()
        
        # Datei kopieren falls vorhanden
        if gal_file:
            gal_file = self._copy_gal_file(gal_file, name)
        
        system_id = insert_system(self.db_path, name, gal_file, gal_link, next_check)

        insert_system_part(self.db_path, system_id, 1, part1)

        insert_system_part(self.db_path, system_id, 2, part2)
        insert_system_part(self.db_path, system_id, 3, part3)

        self.status_var.set(f"System gespeichert (ID {system_id})")
        
        # Felder löschen
        self.system_name_entry.delete(0, tk.END)
        self.system_next_check_entry.delete(0, tk.END)
        self.system_gal_file_var.set("")
        self.system_gal_link_entry.delete(0, tk.END)
        for entries in [self.system_part2, self.system_part3]:
            for entry in entries.values():
                entry.delete(0, tk.END)
        
        self.refresh_lists()

    def refresh_lists(self):
        self.product_list.delete(0, tk.END)
        self.system_list.delete(0, tk.END)

        self.products = fetch_products(self.db_path)
        self.systems = fetch_systems(self.db_path)

        for _, label in self.products:
            self.product_list.insert(tk.END, label)
        for _, label in self.systems:
            self.system_list.insert(tk.END, label)

        if hasattr(self, "due_tree"):
            self.refresh_due_list()

    def _selected_product_id(self) -> int | None:
        selection = self.product_list.curselection()
        if not selection:
            return None
        index = selection[0]
        return self.products[index][0]

    def _selected_system_id(self) -> int | None:
        selection = self.system_list.curselection()
        if not selection:
            return None
        index = selection[0]
        return self.systems[index][0]

    def _on_product_double_click(self, _event=None):
        product_id = self._selected_product_id()
        if product_id is None:
            return
        self.edit_product(product_id)

    def _on_system_double_click(self, _event=None):
        system_id = self._selected_system_id()
        if system_id is None:
            return
        self.edit_system(system_id)

    def edit_product(self, product_id: int):
        product = fetch_product_details(self.db_path, product_id)
        if not product:
            messagebox.showerror("Fehler", "Produkt wurde nicht gefunden.")
            return

        dialog = ProductEditDialog(self, product)
        self.wait_window(dialog)
        if not dialog.result:
            return

        if not messagebox.askyesno("Bestätigung", "Soll dieses Produkt wirklich geändert werden?"):
            return

        data = {
            **product,
            **dialog.result,
        }
        if not self._validate_date_or_empty(data.get("naechste_pruefung_am", "")):
            messagebox.showerror("Fehler", "Nächste Prüfung: Datum bitte im Format YYYY-MM-DD eingeben.")
            return
        gal_file = data.get("gal_datei", "")
        if gal_file and Path(gal_file) != Path(product.get("gal_datei", "")):
            data["gal_datei"] = self._copy_gal_file(gal_file, data.get("produktname", "produkt"))

        update_product(self.db_path, product_id, data)
        refresh_verleih_item_label(self.db_path, item_type="product", item_id=product_id)
        self.refresh_lists()
        self.status_var.set(f"Produkt #{product_id} aktualisiert")

    def edit_system(self, system_id: int):
        details = fetch_system_details(self.db_path, system_id)
        if not details:
            messagebox.showerror("Fehler", "System wurde nicht gefunden.")
            return

        dialog = SystemEditDialog(self, details["system"], details["parts"])
        self.wait_window(dialog)
        if not dialog.result:
            return

        if not messagebox.askyesno("Bestätigung", "Soll dieses System wirklich geändert werden?"):
            return

        system_data = {
            **details["system"],
            **dialog.result["system"],
        }
        if not self._validate_date_or_empty(system_data.get("naechste_pruefung_am", "")):
            messagebox.showerror("Fehler", "Nächste Prüfung: Datum bitte im Format YYYY-MM-DD eingeben.")
            return
        gal_file = system_data.get("gal_datei", "")
        if gal_file and Path(gal_file) != Path(details["system"].get("gal_datei", "")):
            system_data["gal_datei"] = self._copy_gal_file(gal_file, system_data.get("name", "system"))

        update_system(self.db_path, system_id, system_data, dialog.result["parts"])
        refresh_verleih_item_label(self.db_path, item_type="system", item_id=system_id)
        self.refresh_lists()
        self.status_var.set(f"System #{system_id} aktualisiert")

    def refresh_due_list(self):
        if not hasattr(self, "due_tree"):
            return
        self.due_tree.delete(*self.due_tree.get_children())
        self.due_items = fetch_due_items(self.db_path)

        for item in self.due_items:
            key = (str(item["item_type"]), int(item["item_id"]))
            tags = []
            if int(item.get("is_locked", 0)):
                tags.append("locked")
            if key in self.pending_pruefungen:
                tags.append("pending")
            self.due_tree.insert(
                "",
                tk.END,
                iid=f"{item['item_type']}:{item['item_id']}",
                values=(
                    item.get("item_label", ""),
                    item.get("naechste_pruefung_am", "") or "-",
                    "Ja" if int(item.get("is_locked", 0)) else "Nein",
                    item.get("last_psa_pruefung_am", "") or "-",
                ),
                tags=tuple(tags),
            )

    def _open_due_item(self, _event=None):
        selection = self.due_tree.selection()
        if not selection:
            return
        iid = selection[0]
        item_type, item_id_str = iid.split(":", 1)
        item_id = int(item_id_str)
        item = next((x for x in self.due_items if x["item_type"] == item_type and int(x["item_id"]) == item_id), None)
        if not item:
            return

        initial = self.pending_pruefungen.get((item_type, item_id))
        vorgaenge = fetch_item_vorgaenge(self.db_path, item_type=item_type, item_id=item_id)
        dlg = ItemPruefungDialog(self, item=item, vorgaenge=vorgaenge, initial=initial)
        self.wait_window(dlg)
        if not dlg.result:
            return

        if dlg.result.get("delete") and has_active_loans(self.db_path, item_type=item_type, item_id=item_id):
            messagebox.showerror("Fehler", "Löschen nicht möglich: aktive Reservierung/Ausleihe vorhanden.")
            return

        if dlg.result.get("delete"):
            if not messagebox.askyesno("Löschen", "Soll dieses Produkt/System wirklich gelöscht werden?"):
                return

        self.pending_pruefungen[(item_type, item_id)] = {
            "item_type": item_type,
            "item_id": item_id,
            "item_label": item.get("item_label", ""),
            "psa_done": bool(dlg.result.get("psa_done", False)),
            "kommentar": str(dlg.result.get("kommentar", "")),
            "naechste_pruefung_am": str(dlg.result.get("naechste_pruefung_am", "")),
            "delete": bool(dlg.result.get("delete", False)),
            "lock": bool(dlg.result.get("lock", False)),
            "lock_comment": str(dlg.result.get("lock_comment", "")),
            "unlock": bool(dlg.result.get("unlock", False)),
            "unlock_comment": str(dlg.result.get("unlock_comment", "")),
        }
        self.refresh_due_list()

    def commit_pending_pruefungen(self):
        if not self.pending_pruefungen:
            messagebox.showinfo("Hinweis", "Keine offenen Prüfungsänderungen vorhanden.")
            return

        sign = SachkundigSignDialog(self)
        self.wait_window(sign)
        if not sign.result:
            return

        now = _today_iso()
        report_items: list[dict] = []

        for key, pending in list(self.pending_pruefungen.items()):
            item_type = pending["item_type"]
            item_id = int(pending["item_id"])
            action = "update"

            if pending.get("delete"):
                archived_ok = archive_item_with_history(
                    self.db_path,
                    item_type=item_type,
                    item_id=item_id,
                    item_label=str(pending.get("item_label", "")),
                    kommentar=str(pending.get("kommentar", "")),
                    archived_by=str(sign.result.get("name", "")),
                )
                if not archived_ok:
                    messagebox.showerror(
                        "Fehler",
                        f"Archivierung fehlgeschlagen für {pending.get('item_label', '')}. Löschen wurde nicht durchgeführt.",
                    )
                    continue
                delete_item(self.db_path, item_type=item_type, item_id=item_id)
                report_items.append({
                    "item_type": item_type,
                    "item_id": item_id,
                    "item_label": pending.get("item_label", ""),
                    "psa_done": pending.get("psa_done", False),
                    "kommentar": pending.get("kommentar", ""),
                    "naechste_pruefung_am": pending.get("naechste_pruefung_am", ""),
                    "aktion": "archived+delete",
                })
                continue

            if pending.get("unlock"):
                changed = unlock_item(
                    self.db_path,
                    item_type=item_type,
                    item_id=item_id,
                    unlock_comment=pending.get("unlock_comment", ""),
                )
                if changed:
                    action = "unlock+update"

            if pending.get("lock"):
                changed = lock_item(
                    self.db_path,
                    item_type=item_type,
                    item_id=item_id,
                    lock_comment=pending.get("lock_comment", ""),
                )
                if changed:
                    action = "lock+update" if action == "update" else f"{action}+lock"

            if item_type == "product":
                details = fetch_product_details(self.db_path, item_id)
                if not details:
                    continue
                details["naechste_pruefung_am"] = pending.get("naechste_pruefung_am", "")
                if pending.get("psa_done"):
                    details["last_psa_pruefung_am"] = now
                    details["last_psa_pruefung_kommentar"] = pending.get("kommentar", "")
                if pending.get("lock"):
                    details["last_psa_durchgefallen_am"] = datetime.now().isoformat(timespec="seconds")
                update_product(self.db_path, item_id, details)
            else:
                details = fetch_system_details(self.db_path, item_id)
                if not details:
                    continue
                system_data = details["system"]
                system_data["naechste_pruefung_am"] = pending.get("naechste_pruefung_am", "")
                if pending.get("psa_done"):
                    system_data["last_psa_pruefung_am"] = now
                    system_data["last_psa_pruefung_kommentar"] = pending.get("kommentar", "")
                if pending.get("lock"):
                    system_data["last_psa_durchgefallen_am"] = datetime.now().isoformat(timespec="seconds")
                update_system(self.db_path, item_id, system_data, details["parts"])

            refresh_verleih_item_label(self.db_path, item_type=item_type, item_id=item_id)
            report_items.append({
                "item_type": item_type,
                "item_id": item_id,
                "item_label": pending.get("item_label", ""),
                "psa_done": pending.get("psa_done", False),
                "kommentar": pending.get("kommentar", ""),
                "naechste_pruefung_am": pending.get("naechste_pruefung_am", ""),
                "aktion": action,
            })

        report_id = create_psa_report(
            self.db_path,
            signer_name=sign.result.get("name", ""),
            signature_data=sign.result.get("signature", ""),
            items=report_items,
        )

        self.pending_pruefungen.clear()
        self.refresh_lists()
        self.refresh_due_list()
        self.refresh_reports_list()
        self.refresh_archive_list()
        self.status_var.set(f"Prüfungsrunde gespeichert (Bericht #{report_id})")

    def refresh_reports_list(self):
        if not hasattr(self, "report_tree"):
            return
        self.report_tree.delete(*self.report_tree.get_children())
        for r in fetch_reports(self.db_path):
            self.report_tree.insert(
                "",
                tk.END,
                iid=str(r.get("id")),
                values=(r.get("id"), r.get("created_at"), r.get("sachkundiger_name"), r.get("item_count")),
            )

    def refresh_archive_list(self):
        if not hasattr(self, "archive_tree"):
            return
        self.archive_tree.delete(*self.archive_tree.get_children())
        self.archived_items = fetch_archived_items(ARCHIVE_DB_PATH)
        for item in self.archived_items:
            iid = str(item.get("archived_item_id"))
            self.archive_tree.insert(
                "",
                tk.END,
                iid=iid,
                values=(
                    item.get("item_label", ""),
                    item.get("archived_at", ""),
                    item.get("archived_by", "") or "-",
                    item.get("kommentar", "") or "-",
                ),
            )

    def generate_archive_haftung(self):
        if not hasattr(self, "archive_tree"):
            return
        selection = self.archive_tree.selection()
        if not selection:
            messagebox.showerror("Fehler", "Bitte zuerst einen archivierten Eintrag auswählen.")
            return
        archived_id = int(selection[0])
        item = next((x for x in getattr(self, "archived_items", []) if int(x.get("archived_item_id", 0)) == archived_id), None)
        if not item:
            messagebox.showerror("Fehler", "Archiv-Eintrag wurde nicht gefunden.")
            return

        item_type = str(item.get("item_type") or "")
        source_item_id = int(item.get("source_item_id") or 0)
        if source_item_id <= 0:
            messagebox.showerror("Fehler", "Ungültige Archiv-ID.")
            return

        try:
            if item_type == "product":
                generate_haftungsausschluss_pdf_from_db(product_id=source_item_id, system_id=None, db_path=ARCHIVE_DB_PATH)
            else:
                generate_haftungsausschluss_pdf_from_db(product_id=None, system_id=source_item_id, db_path=ARCHIVE_DB_PATH)
            messagebox.showinfo("OK", "Haftungsausschluss aus dem Archiv wurde erzeugt.")
        except Exception as e:
            messagebox.showerror("Fehler", f"Konnte Haftungsausschluss nicht erzeugen: {e}")

    def _open_archive_item(self, _event=None):
        if not hasattr(self, "archive_tree"):
            return
        selection = self.archive_tree.selection()
        if not selection:
            return
        archived_id = int(selection[0])
        item = next((x for x in getattr(self, "archived_items", []) if int(x.get("archived_item_id", 0)) == archived_id), None)
        if not item:
            messagebox.showerror("Fehler", "Archiv-Eintrag wurde nicht gefunden.")
            return

        item_type = str(item.get("item_type") or "")
        source_item_id = int(item.get("source_item_id") or 0)
        if source_item_id <= 0:
            messagebox.showerror("Fehler", "Ungültige Archiv-ID.")
            return

        if item_type == "product":
            details = fetch_product_details(ARCHIVE_DB_PATH, source_item_id)
        else:
            details = fetch_system_details(ARCHIVE_DB_PATH, source_item_id)

        vorgaenge = fetch_item_vorgaenge(ARCHIVE_DB_PATH, item_type=item_type, item_id=source_item_id)
        ArchivedItemDetailsDialog(self, archived_item=item, details=details, vorgaenge=vorgaenge)

    def _open_report_details(self, _event=None):
        selection = self.report_tree.selection()
        if not selection:
            return
        report_id = int(selection[0])
        details = fetch_report_details(self.db_path, report_id)
        if not details:
            messagebox.showerror("Fehler", "Bericht nicht gefunden.")
            return
        ReportDetailsDialog(self, details)

    def generate_selected_pdfs(self):
        product_id = self._selected_product_id()
        system_id = self._selected_system_id()

        if self.make_einsatz.get():
            if product_id is None and system_id is None:
                messagebox.showerror("Fehler", "Bitte Produkt oder System auswählen.")
                return
            generate_einsatzdokumentation_pdf_from_db(
                product_id=product_id, system_id=system_id
            )

        if self.make_pd.get():
            generate_pd_pdf_from_db(product_id=product_id, system_id=system_id)

        if self.make_haftung.get():
            generate_haftungsausschluss_pdf_from_db(product_id=product_id, system_id=system_id)

        self.status_var.set("PDFs erfolgreich erstellt")


def main():
    init_db(DEFAULT_DB_PATH)
    ensure_db_schema(DEFAULT_DB_PATH)
    root = tk.Tk()
    root.title("PSA Manager")
    root.geometry("1000x700")
    app = PSAApp(root, DEFAULT_DB_PATH)
    root.mainloop()


if __name__ == "__main__":
    main()
