import json
import os
import sqlite3
import tkinter as tk
import webbrowser
import calendar
from datetime import date, datetime, timedelta
from pathlib import Path
from tkinter import messagebox, ttk

from generate_PDF import DEFAULT_DB_PATH, init_db


def _db_connect(db_path: Path):
    return sqlite3.connect(db_path)


def ensure_verleih_schema(db_path: Path) -> None:
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

        existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(verleih_planung)").fetchall()}
        if "status" not in existing_cols:
            conn.execute("ALTER TABLE verleih_planung ADD COLUMN status TEXT NOT NULL DEFAULT 'lent'")
        if "checkout_at" not in existing_cols:
            conn.execute("ALTER TABLE verleih_planung ADD COLUMN checkout_at TEXT")
        if "returned_at" not in existing_cols:
            conn.execute("ALTER TABLE verleih_planung ADD COLUMN returned_at TEXT")
        if "entleiher_email" not in existing_cols:
            conn.execute("ALTER TABLE verleih_planung ADD COLUMN entleiher_email TEXT")
        if "entleiher_telefon" not in existing_cols:
            conn.execute("ALTER TABLE verleih_planung ADD COLUMN entleiher_telefon TEXT")
        if "entleiher_adresse" not in existing_cols:
            conn.execute("ALTER TABLE verleih_planung ADD COLUMN entleiher_adresse TEXT")
        if "ausgebende_person" not in existing_cols:
            conn.execute("ALTER TABLE verleih_planung ADD COLUMN ausgebende_person TEXT")
        if "quick_check_out" not in existing_cols:
            conn.execute("ALTER TABLE verleih_planung ADD COLUMN quick_check_out INTEGER NOT NULL DEFAULT 0")
        if "gal_provided_out" not in existing_cols:
            conn.execute("ALTER TABLE verleih_planung ADD COLUMN gal_provided_out INTEGER NOT NULL DEFAULT 0")
        if "return_comment" not in existing_cols:
            conn.execute("ALTER TABLE verleih_planung ADD COLUMN return_comment TEXT")
        if "ruecknehmende_person" not in existing_cols:
            conn.execute("ALTER TABLE verleih_planung ADD COLUMN ruecknehmende_person TEXT")
        if "return_signature_data" not in existing_cols:
            conn.execute("ALTER TABLE verleih_planung ADD COLUMN return_signature_data TEXT")
        if "quick_check_return" not in existing_cols:
            conn.execute("ALTER TABLE verleih_planung ADD COLUMN quick_check_return INTEGER NOT NULL DEFAULT 0")

        # PSA-Spalten absichern (falls DB nur über Verleih-GUI initialisiert wurde)
        product_cols = {row[1] for row in conn.execute("PRAGMA table_info(products)").fetchall()}
        system_cols = {row[1] for row in conn.execute("PRAGMA table_info(systems)").fetchall()}
        if "naechste_pruefung_am" not in product_cols:
            conn.execute("ALTER TABLE products ADD COLUMN naechste_pruefung_am TEXT")
        if "naechste_pruefung_am" not in system_cols:
            conn.execute("ALTER TABLE systems ADD COLUMN naechste_pruefung_am TEXT")

        conn.execute(
            """
            UPDATE verleih_planung
            SET status = CASE
                WHEN COALESCE(signature_data, '') = '' THEN 'reserved'
                ELSE 'lent'
            END
            WHERE status IS NULL OR status = ''
            """
        )


def fetch_products(db_path: Path) -> list[dict]:
    with _db_connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT id, einzelidentifikation, produktbezeichnung, produktname, seriennummer
            FROM products
            ORDER BY COALESCE(einzelidentifikation, '') COLLATE NOCASE ASC,
                     id ASC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def fetch_systems(db_path: Path) -> list[dict]:
    with _db_connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT s.id, sp.einzelidentifikation, s.name
            FROM systems s
            LEFT JOIN system_parts sp ON sp.system_id = s.id AND sp.part_index = 1
            ORDER BY COALESCE(sp.einzelidentifikation, '') COLLATE NOCASE ASC,
                     s.id ASC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def fetch_distinct_produktbezeichnungen(db_path: Path) -> list[str]:
    with _db_connect(db_path) as conn:
        rows = conn.execute(
            """
                        SELECT DISTINCT value
                        FROM (
                                SELECT TRIM(COALESCE(produktbezeichnung, '')) AS value
                                FROM products
                                WHERE TRIM(COALESCE(produktbezeichnung, '')) <> ''
                                    AND COALESCE(naechste_pruefung_am, '') <> ''
                                    AND naechste_pruefung_am >= DATE('now')

                                UNION

                                SELECT TRIM(COALESCE(name, '')) AS value
                                FROM systems
                                WHERE TRIM(COALESCE(name, '')) <> ''
                                    AND COALESCE(naechste_pruefung_am, '') <> ''
                                    AND naechste_pruefung_am >= DATE('now')
                        ) vals
                        ORDER BY value COLLATE NOCASE ASC
            """
        ).fetchall()
    return [str(row[0]) for row in rows]


def is_item_psa_current(db_path: Path, *, item_type: str, item_id: int) -> bool:
    with _db_connect(db_path) as conn:
        if item_type == "product":
            row = conn.execute(
                """
                SELECT 1
                FROM products
                WHERE id = ?
                  AND COALESCE(naechste_pruefung_am, '') <> ''
                  AND naechste_pruefung_am >= DATE('now')
                LIMIT 1
                """,
                (item_id,),
            ).fetchone()
        else:
            row = conn.execute(
                """
                SELECT 1
                FROM systems
                WHERE id = ?
                  AND COALESCE(naechste_pruefung_am, '') <> ''
                  AND naechste_pruefung_am >= DATE('now')
                LIMIT 1
                """,
                (item_id,),
            ).fetchone()
    return row is not None


def fetch_item_next_pruefung_am(db_path: Path, *, item_type: str, item_id: int) -> str:
    with _db_connect(db_path) as conn:
        if item_type == "product":
            row = conn.execute(
                "SELECT COALESCE(naechste_pruefung_am, '') FROM products WHERE id = ?",
                (item_id,),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT COALESCE(naechste_pruefung_am, '') FROM systems WHERE id = ?",
                (item_id,),
            ).fetchone()
    if not row:
        return ""
    return str(row[0] or "").strip()


def fetch_pending_verleihplaene(db_path: Path) -> list[dict]:
    with _db_connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT
                v.id,
                v.item_type,
                v.item_id,
                CASE
                    WHEN v.item_type = 'product' THEN
                        ('EI: ' || COALESCE(p.einzelidentifikation, '-') || ' | PB: ' || COALESCE(p.produktbezeichnung, '-') ||
                         ' | Name: ' || COALESCE(p.produktname, 'Ohne Name') || ' | SN: ' || COALESCE(p.seriennummer, '-'))
                    WHEN v.item_type = 'system' THEN
                        ('EI: ' || COALESCE(sp.einzelidentifikation, '-') ||
                         ' | Name: ' || COALESCE(s.name, 'System'))
                    ELSE COALESCE(v.item_label, '')
                END AS item_label,
                v.von_datum,
                v.rueckgabe_datum,
                v.entleiher,
                v.status,
                v.checkout_at,
                v.returned_at,
                v.created_at
            FROM verleih_planung v
            LEFT JOIN products p ON v.item_type = 'product' AND p.id = v.item_id
            LEFT JOIN systems s ON v.item_type = 'system' AND s.id = v.item_id
            LEFT JOIN system_parts sp ON sp.system_id = s.id AND sp.part_index = 1
            WHERE COALESCE(status, 'lent') IN ('reserved', 'lent')
              AND (
                    (COALESCE(status, 'lent') = 'reserved' AND von_datum >= DATE('now'))
                 OR COALESCE(status, 'lent') = 'lent'
              )
            ORDER BY COALESCE(sp.einzelidentifikation, p.einzelidentifikation, '') COLLATE NOCASE ASC,
                     rueckgabe_datum ASC,
                     von_datum ASC,
                     v.id ASC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def fetch_verleih_by_id(db_path: Path, verleih_id: int) -> dict | None:
    with _db_connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT *
            FROM verleih_planung
            WHERE id = ?
            """,
            (verleih_id,),
        ).fetchone()
    return dict(row) if row else None


def fetch_calendar_entries(db_path: Path, *, from_date: str, to_date: str) -> list[dict]:
    with _db_connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT
                v.id,
                CASE
                    WHEN v.item_type = 'product' THEN
                        ('EI: ' || COALESCE(p.einzelidentifikation, '-') || ' | PB: ' || COALESCE(p.produktbezeichnung, '-') ||
                         ' | Name: ' || COALESCE(p.produktname, 'Ohne Name') || ' | SN: ' || COALESCE(p.seriennummer, '-'))
                    WHEN v.item_type = 'system' THEN
                        ('EI: ' || COALESCE(sp.einzelidentifikation, '-') ||
                         ' | Name: ' || COALESCE(s.name, 'System'))
                    ELSE COALESCE(v.item_label, '')
                END AS item_label,
                v.von_datum,
                v.rueckgabe_datum,
                v.entleiher,
                v.status
            FROM verleih_planung v
            LEFT JOIN products p ON v.item_type = 'product' AND p.id = v.item_id
            LEFT JOIN systems s ON v.item_type = 'system' AND s.id = v.item_id
            LEFT JOIN system_parts sp ON sp.system_id = s.id AND sp.part_index = 1
            WHERE COALESCE(status, 'lent') IN ('reserved', 'lent')
              AND von_datum <= ?
              AND rueckgabe_datum >= ?
            ORDER BY COALESCE(sp.einzelidentifikation, p.einzelidentifikation, '') COLLATE NOCASE ASC,
                     von_datum ASC,
                     rueckgabe_datum ASC,
                     item_label COLLATE NOCASE ASC
            """,
            (to_date, from_date),
        ).fetchall()
    return [dict(row) for row in rows]


def insert_verleih(
    db_path: Path,
    *,
    item_type: str,
    item_id: int,
    item_label: str,
    von_datum: str,
    rueckgabe_datum: str,
    entleiher: str,
    ausgebende_person: str,
    entleiher_email: str,
    entleiher_telefon: str,
    entleiher_adresse: str,
    signature_data: str,
    quick_check_out: bool,
    gal_provided_out: bool,
    status: str,
    checkout_at: str | None,
) -> int:
    with _db_connect(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO verleih_planung (
                item_type, item_id, item_label, von_datum, rueckgabe_datum,
                entleiher, ausgebende_person, entleiher_email, entleiher_telefon, entleiher_adresse,
                signature_data, quick_check_out, gal_provided_out, status, checkout_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item_type,
                item_id,
                item_label,
                von_datum,
                rueckgabe_datum,
                entleiher,
                ausgebende_person,
                entleiher_email,
                entleiher_telefon,
                entleiher_adresse,
                signature_data,
                1 if quick_check_out else 0,
                1 if gal_provided_out else 0,
                status,
                checkout_at,
                datetime.now().isoformat(timespec="seconds"),
            ),
        )
        return cur.lastrowid


def checkout_reserved_verleih(
    db_path: Path,
    verleih_id: int,
    signature_data: str,
    quick_check_out: bool,
    gal_provided_out: bool,
    ausgebende_person: str,
) -> None:
    with _db_connect(db_path) as conn:
        conn.execute(
            """
            UPDATE verleih_planung
            SET status = 'lent',
                signature_data = ?,
                quick_check_out = ?,
                gal_provided_out = ?,
                ausgebende_person = ?,
                checkout_at = ?,
                returned_at = NULL
            WHERE id = ?
              AND COALESCE(status, 'lent') = 'reserved'
            """,
            (
                signature_data,
                1 if quick_check_out else 0,
                1 if gal_provided_out else 0,
                ausgebende_person,
                datetime.now().isoformat(timespec="seconds"),
                verleih_id,
            ),
        )


def confirm_return(
    db_path: Path,
    verleih_id: int,
    *,
    return_comment: str,
    ruecknehmende_person: str,
    return_signature_data: str,
    quick_check_return: bool,
) -> None:
    with _db_connect(db_path) as conn:
        conn.execute(
            """
            UPDATE verleih_planung
            SET status = 'returned',
                return_comment = ?,
                                ruecknehmende_person = ?,
                return_signature_data = ?,
                quick_check_return = ?,
                returned_at = ?
            WHERE id = ?
              AND COALESCE(status, 'lent') = 'lent'
            """,
            (
                return_comment,
                                ruecknehmende_person,
                return_signature_data,
                1 if quick_check_return else 0,
                datetime.now().isoformat(timespec="seconds"),
                verleih_id,
            ),
        )


def lock_item(
    db_path: Path,
    *,
    item_type: str,
    item_id: int,
    lock_comment: str,
) -> None:
    with _db_connect(db_path) as conn:
        conn.execute(
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


def is_item_locked(db_path: Path, *, item_type: str, item_id: int) -> bool:
    with _db_connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT is_locked
            FROM item_locks
            WHERE item_type = ? AND item_id = ?
            """,
            (item_type, item_id),
        ).fetchone()
    return bool(row and int(row[0]) == 1)


def fetch_item_gal_data(db_path: Path, *, item_type: str, item_id: int) -> tuple[str, str]:
    with _db_connect(db_path) as conn:
        if item_type == "product":
            row = conn.execute(
                "SELECT COALESCE(gal_datei, ''), COALESCE(gal_link, '') FROM products WHERE id = ?",
                (item_id,),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT COALESCE(gal_datei, ''), COALESCE(gal_link, '') FROM systems WHERE id = ?",
                (item_id,),
            ).fetchone()
    if not row:
        return "", ""
    return str(row[0] or ""), str(row[1] or "")


def open_reference(target: str) -> None:
    value = (target or "").strip()
    if not value:
        messagebox.showerror("Fehler", "Kein GAL-Link oder keine GAL-Datei vorhanden.")
        return

    if value.lower().startswith("http://") or value.lower().startswith("https://"):
        webbrowser.open(value)
        return

    path = Path(value)
    if path.exists():
        os.startfile(str(path))
        return

    messagebox.showerror("Fehler", "GAL-Link/Datei konnte nicht geöffnet werden.")


def update_verleih_contact(
    db_path: Path,
    verleih_id: int,
    *,
    entleiher: str,
    entleiher_email: str,
    entleiher_telefon: str,
    entleiher_adresse: str,
) -> None:
    with _db_connect(db_path) as conn:
        conn.execute(
            """
            UPDATE verleih_planung
            SET entleiher = ?,
                entleiher_email = ?,
                entleiher_telefon = ?,
                entleiher_adresse = ?
            WHERE id = ?
              AND COALESCE(status, 'lent') IN ('reserved', 'lent')
            """,
            (entleiher, entleiher_email, entleiher_telefon, entleiher_adresse, verleih_id),
        )


def find_available_items(
    db_path: Path,
    suchtext: str,
    von_datum: str,
    bis_datum: str,
) -> list[dict]:
    with _db_connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT *
            FROM (
                SELECT
                    'product' AS item_type,
                    p.id AS item_id,
                    p.produktbezeichnung AS suchfeld,
                    ('EI: ' || COALESCE(p.einzelidentifikation, '-') || ' | PB: ' || COALESCE(p.produktbezeichnung, '-') || ' | '
                     || COALESCE(p.produktname, 'Ohne Name') || ' | SN: ' || COALESCE(p.seriennummer, '-')) AS item_label
                FROM products p
                WHERE (
                    COALESCE(p.produktbezeichnung, '') LIKE ?
                    OR COALESCE(p.produktname, '') LIKE ?
                    OR COALESCE(p.seriennummer, '') LIKE ?
                )
                                    AND COALESCE(p.naechste_pruefung_am, '') <> ''
                                    AND p.naechste_pruefung_am >= DATE('now')
                  AND NOT EXISTS (
                    SELECT 1
                    FROM verleih_planung v
                    WHERE v.item_type = 'product'
                      AND v.item_id = p.id
                      AND COALESCE(v.status, 'lent') IN ('reserved', 'lent')
                      AND v.von_datum <= ?
                      AND v.rueckgabe_datum >= ?
                  )
                                    AND NOT EXISTS (
                                        SELECT 1
                                        FROM item_locks l
                                        WHERE l.item_type = 'product'
                                            AND l.item_id = p.id
                                            AND l.is_locked = 1
                                    )

                UNION ALL

                SELECT
                    'system' AS item_type,
                    s.id AS item_id,
                    s.name AS suchfeld,
                    ('EI: ' || COALESCE(sp.einzelidentifikation, '-') || ' | Name: ' || COALESCE(s.name, 'System')) AS item_label
                FROM systems s
                LEFT JOIN system_parts sp ON sp.system_id = s.id AND sp.part_index = 1
                WHERE COALESCE(s.name, '') LIKE ?
                                    AND COALESCE(s.naechste_pruefung_am, '') <> ''
                                    AND s.naechste_pruefung_am >= DATE('now')
                                    AND NOT EXISTS (
                                        SELECT 1
                                        FROM item_locks l
                                        WHERE l.item_type = 'system'
                                            AND l.item_id = s.id
                                            AND l.is_locked = 1
                                    )
                  AND NOT EXISTS (
                    SELECT 1
                    FROM verleih_planung v
                    WHERE v.item_type = 'system'
                      AND v.item_id = s.id
                      AND COALESCE(v.status, 'lent') IN ('reserved', 'lent')
                      AND v.von_datum <= ?
                      AND v.rueckgabe_datum >= ?
                  )
            ) all_items
            ORDER BY COALESCE(item_label, '') COLLATE NOCASE ASC
            """,
            (
                f"%{suchtext}%",
                f"%{suchtext}%",
                f"%{suchtext}%",
                bis_datum,
                von_datum,
                f"%{suchtext}%",
                bis_datum,
                von_datum,
            ),
        ).fetchall()
    return [dict(row) for row in rows]


def is_item_available(
    db_path: Path,
    *,
    item_type: str,
    item_id: int,
    von_datum: str,
    bis_datum: str,
) -> bool:
    if not is_item_psa_current(db_path, item_type=item_type, item_id=item_id):
        return False

    if is_item_locked(db_path, item_type=item_type, item_id=item_id):
        return False

    with _db_connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT 1
            FROM verleih_planung v
            WHERE v.item_type = ?
              AND v.item_id = ?
              AND COALESCE(v.status, 'lent') IN ('reserved', 'lent')
              AND v.von_datum <= ?
              AND v.rueckgabe_datum >= ?
            LIMIT 1
            """,
            (item_type, item_id, bis_datum, von_datum),
        ).fetchone()
    return row is None


def parse_date(value: str) -> datetime:
    return datetime.strptime(value.strip(), "%Y-%m-%d")


def draw_signature_on_canvas(canvas: tk.Canvas, signature_data: str, *, width: int = 2) -> bool:
    canvas.delete("all")
    if not signature_data:
        canvas.create_text(10, 10, anchor="nw", text="Keine Unterschrift gespeichert", fill="#666")
        return False

    try:
        strokes = json.loads(signature_data)
    except (TypeError, json.JSONDecodeError):
        canvas.create_text(10, 10, anchor="nw", text="Unterschrift kann nicht geladen werden", fill="#b00020")
        return False

    has_lines = False
    for stroke in strokes:
        if not isinstance(stroke, list) or len(stroke) < 2:
            continue
        for i in range(1, len(stroke)):
            x1, y1 = stroke[i - 1]
            x2, y2 = stroke[i]
            canvas.create_line(x1, y1, x2, y2, width=width, fill="black", capstyle=tk.ROUND, smooth=True)
            has_lines = True

    if not has_lines:
        canvas.create_text(10, 10, anchor="nw", text="Keine Unterschrift gespeichert", fill="#666")
    return has_lines


class SignatureDialog(tk.Toplevel):
    def __init__(self, master: tk.Misc, context_text: str = ""):
        super().__init__(master)
        self.title("Digitale Unterschrift")
        self.geometry("580x380")
        self.resizable(False, False)
        self.transient(master)
        self.grab_set()

        self.result: str | None = None
        self._strokes: list[list[tuple[int, int]]] = []
        self._current_stroke: list[tuple[int, int]] = []

        ttk.Label(self, text="Mit gedrückter Maustaste unterschreiben").pack(anchor="w", padx=8, pady=(8, 2))

        if context_text:
            ttk.Label(self, text=context_text, justify="left", wraplength=560).pack(anchor="w", padx=8, pady=(0, 6))

        self.canvas = tk.Canvas(self, width=560, height=240, bg="white", highlightthickness=1, highlightbackground="#888")
        self.canvas.pack(padx=8, pady=6)

        self.canvas.bind("<ButtonPress-1>", self._start_stroke)
        self.canvas.bind("<B1-Motion>", self._draw)
        self.canvas.bind("<ButtonRelease-1>", self._end_stroke)

        button_frame = ttk.Frame(self)
        button_frame.pack(fill="x", padx=8, pady=(4, 8))

        ttk.Button(button_frame, text="Leeren", command=self.clear).pack(side=tk.LEFT)
        ttk.Button(button_frame, text="Übernehmen", command=self.save).pack(side=tk.RIGHT)
        ttk.Button(button_frame, text="Abbrechen", command=self.cancel).pack(side=tk.RIGHT, padx=(0, 6))

    def _start_stroke(self, event: tk.Event):
        self._current_stroke = [(event.x, event.y)]

    def _draw(self, event: tk.Event):
        if not self._current_stroke:
            self._current_stroke = [(event.x, event.y)]
            return
        last_x, last_y = self._current_stroke[-1]
        self.canvas.create_line(last_x, last_y, event.x, event.y, width=2, fill="black", capstyle=tk.ROUND, smooth=True)
        self._current_stroke.append((event.x, event.y))

    def _end_stroke(self, _: tk.Event):
        if len(self._current_stroke) >= 2:
            self._strokes.append(self._current_stroke[:])
        self._current_stroke = []

    def clear(self):
        self.canvas.delete("all")
        self._strokes.clear()
        self._current_stroke = []

    def save(self):
        if not self._strokes:
            messagebox.showwarning("Hinweis", "Bitte zuerst unterschreiben.", parent=self)
            return
        self.result = json.dumps(self._strokes)
        self.destroy()

    def cancel(self):
        self.result = None
        self.destroy()


class CheckoutDialog(tk.Toplevel):
    def __init__(self, master: tk.Misc, context_text: str = "", gal_display: str = "", gal_target: str = ""):
        super().__init__(master)
        self.title("Reservierung ausleihen")
        self.geometry("620x620")
        self.minsize(620, 560)
        self.resizable(True, True)
        self.transient(master)
        self.grab_set()

        self.result: dict | None = None
        self._strokes: list[list[tuple[int, int]]] = []
        self._current_stroke: list[tuple[int, int]] = []
        self.quick_check_var = tk.BooleanVar(value=False)
        self.gal_provided_var = tk.BooleanVar(value=False)
        self.gal_target = gal_target

        ttk.Label(self, text="Digitale Unterschrift für die Ausleihe").pack(anchor="w", padx=8, pady=(8, 2))
        if context_text:
            ttk.Label(self, text=context_text, justify="left", wraplength=560).pack(anchor="w", padx=8, pady=(0, 6))

        if gal_display:
            ttk.Label(self, text=f"GAL: {gal_display}", justify="left", wraplength=560).pack(anchor="w", padx=8, pady=(0, 2))
            ttk.Button(self, text="GAL öffnen", command=lambda: open_reference(self.gal_target)).pack(anchor="w", padx=8, pady=(0, 4))
        else:
            ttk.Label(self, text="GAL: Kein Link/keine Datei hinterlegt", justify="left", wraplength=560).pack(anchor="w", padx=8, pady=(0, 4))

        ttk.Label(self, text="Ausgebende Person").pack(anchor="w", padx=8, pady=(0, 2))
        self.ausgebende_person_entry = ttk.Entry(self)
        self.ausgebende_person_entry.pack(fill="x", padx=8, pady=(0, 6))

        ttk.Checkbutton(self, text="Kurzkontrolle durchgeführt", variable=self.quick_check_var).pack(anchor="w", padx=8, pady=(0, 4))
        ttk.Checkbutton(self, text="GAL bereitgestellt", variable=self.gal_provided_var).pack(anchor="w", padx=8, pady=(0, 4))

        self.canvas = tk.Canvas(self, width=580, height=260, bg="white", highlightthickness=1, highlightbackground="#888")
        self.canvas.pack(padx=8, pady=6)
        self.canvas.bind("<ButtonPress-1>", self._start_stroke)
        self.canvas.bind("<B1-Motion>", self._draw)
        self.canvas.bind("<ButtonRelease-1>", self._end_stroke)

        button_frame = ttk.Frame(self)
        button_frame.pack(fill="x", padx=8, pady=(4, 8))
        ttk.Button(button_frame, text="Leeren", command=self.clear).pack(side=tk.LEFT)
        ttk.Button(button_frame, text="Übernehmen", command=self.save).pack(side=tk.RIGHT)
        ttk.Button(button_frame, text="Abbrechen", command=self.cancel).pack(side=tk.RIGHT, padx=(0, 6))

    def _start_stroke(self, event: tk.Event):
        self._current_stroke = [(event.x, event.y)]

    def _draw(self, event: tk.Event):
        if not self._current_stroke:
            self._current_stroke = [(event.x, event.y)]
            return
        last_x, last_y = self._current_stroke[-1]
        self.canvas.create_line(last_x, last_y, event.x, event.y, width=2, fill="black", capstyle=tk.ROUND, smooth=True)
        self._current_stroke.append((event.x, event.y))

    def _end_stroke(self, _: tk.Event):
        if len(self._current_stroke) >= 2:
            self._strokes.append(self._current_stroke[:])
        self._current_stroke = []

    def clear(self):
        self.canvas.delete("all")
        self._strokes.clear()
        self._current_stroke = []

    def save(self):
        ausgebende_person = self.ausgebende_person_entry.get().strip()
        if not ausgebende_person:
            messagebox.showerror("Fehler", "Bitte die ausgebende Person eintragen.", parent=self)
            return
        if not self.quick_check_var.get():
            messagebox.showerror("Fehler", "Bitte Kurzkontrolle bestätigen.", parent=self)
            return
        if not self.gal_provided_var.get():
            messagebox.showerror("Fehler", "Bitte GAL bereitgestellt bestätigen.", parent=self)
            return
        if not self._strokes:
            messagebox.showwarning("Hinweis", "Bitte zuerst unterschreiben.", parent=self)
            return
        self.result = {
            "signature": json.dumps(self._strokes),
            "quick_check_out": True,
            "gal_provided_out": True,
            "ausgebende_person": ausgebende_person,
        }
        self.destroy()

    def cancel(self):
        self.result = None
        self.destroy()


class ReturnDialog(tk.Toplevel):
    def __init__(self, master: tk.Misc, context_text: str = ""):
        super().__init__(master)
        self.title("Rückgabe bestätigen")
        self.geometry("620x760")
        self.minsize(620, 700)
        self.resizable(True, True)
        self.transient(master)
        self.grab_set()

        self.result: dict | None = None
        self._strokes: list[list[tuple[int, int]]] = []
        self._current_stroke: list[tuple[int, int]] = []
        self.quick_check_var = tk.BooleanVar(value=False)
        self.lock_item_var = tk.BooleanVar(value=False)

        ttk.Label(self, text="Rückgabe mit Kommentar und Unterschrift").pack(anchor="w", padx=8, pady=(8, 2))
        if context_text:
            ttk.Label(self, text=context_text, justify="left", wraplength=560).pack(anchor="w", padx=8, pady=(0, 6))

        ttk.Label(self, text="Kommentar zur Rückgabe").pack(anchor="w", padx=8, pady=(2, 2))
        self.comment_text = tk.Text(self, width=70, height=4)
        self.comment_text.pack(padx=8, pady=(0, 6), fill="x")

        ttk.Checkbutton(self, text="Produkt/System sperren", variable=self.lock_item_var).pack(anchor="w", padx=8, pady=(0, 2))
        ttk.Label(self, text="Kommentar zur Sperrung (Pflicht, falls gesperrt)").pack(anchor="w", padx=8, pady=(0, 2))
        self.lock_comment_entry = ttk.Entry(self)
        self.lock_comment_entry.pack(padx=8, pady=(0, 6), fill="x")

        ttk.Label(self, text="Ausgebende Person (Rückgabe)").pack(anchor="w", padx=8, pady=(0, 2))
        self.ruecknehmende_person_entry = ttk.Entry(self)
        self.ruecknehmende_person_entry.pack(padx=8, pady=(0, 6), fill="x")

        ttk.Checkbutton(self, text="Kurzprüfung bei Rückgabe durchgeführt", variable=self.quick_check_var).pack(anchor="w", padx=8, pady=(0, 4))

        self.canvas = tk.Canvas(self, width=580, height=240, bg="white", highlightthickness=1, highlightbackground="#888")
        self.canvas.pack(padx=8, pady=6)
        self.canvas.bind("<ButtonPress-1>", self._start_stroke)
        self.canvas.bind("<B1-Motion>", self._draw)
        self.canvas.bind("<ButtonRelease-1>", self._end_stroke)

        button_frame = ttk.Frame(self)
        button_frame.pack(fill="x", padx=8, pady=(4, 8))
        ttk.Button(button_frame, text="Leeren", command=self.clear).pack(side=tk.LEFT)
        ttk.Button(button_frame, text="Bestätigen", command=self.save).pack(side=tk.RIGHT)
        ttk.Button(button_frame, text="Abbrechen", command=self.cancel).pack(side=tk.RIGHT, padx=(0, 6))

    def _start_stroke(self, event: tk.Event):
        self._current_stroke = [(event.x, event.y)]

    def _draw(self, event: tk.Event):
        if not self._current_stroke:
            self._current_stroke = [(event.x, event.y)]
            return
        last_x, last_y = self._current_stroke[-1]
        self.canvas.create_line(last_x, last_y, event.x, event.y, width=2, fill="black", capstyle=tk.ROUND, smooth=True)
        self._current_stroke.append((event.x, event.y))

    def _end_stroke(self, _: tk.Event):
        if len(self._current_stroke) >= 2:
            self._strokes.append(self._current_stroke[:])
        self._current_stroke = []

    def clear(self):
        self.canvas.delete("all")
        self._strokes.clear()
        self._current_stroke = []

    def save(self):
        ruecknehmende_person = self.ruecknehmende_person_entry.get().strip()
        if not ruecknehmende_person:
            messagebox.showerror("Fehler", "Bitte die ausgebende Person für die Rückgabe eintragen.", parent=self)
            return
        if not self.quick_check_var.get():
            messagebox.showerror("Fehler", "Bitte Kurzprüfung bei Rückgabe bestätigen.", parent=self)
            return
        if not self._strokes:
            messagebox.showwarning("Hinweis", "Bitte zuerst unterschreiben.", parent=self)
            return

        lock_comment = self.lock_comment_entry.get().strip()
        if self.lock_item_var.get() and not lock_comment:
            messagebox.showerror("Fehler", "Bitte einen Kommentar zur Sperrung eingeben.", parent=self)
            return

        self.result = {
            "comment": self.comment_text.get("1.0", tk.END).strip(),
            "signature": json.dumps(self._strokes),
            "quick_check_return": True,
            "lock_item": self.lock_item_var.get(),
            "lock_comment": lock_comment,
            "ruecknehmende_person": ruecknehmende_person,
        }
        self.destroy()

    def cancel(self):
        self.result = None
        self.destroy()


class VerleihOverviewDialog(tk.Toplevel):
    def __init__(self, master: tk.Misc, plan: dict):
        super().__init__(master)
        self.title(f"Verleihübersicht #{plan.get('id', '')}")
        self.geometry("800x760")
        self.transient(master)

        wrapper = ttk.Frame(self)
        wrapper.pack(fill="both", expand=True, padx=10, pady=10)
        wrapper.columnconfigure(0, weight=1)

        info = ttk.LabelFrame(wrapper, text="Daten")
        info.grid(row=0, column=0, sticky="nsew")
        info.columnconfigure(1, weight=1)

        rows = [
            ("Objekt", plan.get("item_label", "")),
            ("Status", plan.get("status", "")),
            ("Zeitraum", f"{plan.get('von_datum', '')} bis {plan.get('rueckgabe_datum', '')}"),
            ("Entleiher", plan.get("entleiher", "")),
            ("Ausgebende Person", plan.get("ausgebende_person", "") or "-"),
            ("Ausgebende Person (Rückgabe)", plan.get("ruecknehmende_person", "") or "-"),
            ("E-Mail", plan.get("entleiher_email", "")),
            ("Telefon", plan.get("entleiher_telefon", "")),
            ("Adresse", plan.get("entleiher_adresse", "")),
            ("Kurzkontrolle Ausleihe", "Ja" if int(plan.get("quick_check_out") or 0) else "Nein"),
            ("GAL bereitgestellt", "Ja" if int(plan.get("gal_provided_out") or 0) else "Nein"),
            ("Kurzkontrolle Rückgabe", "Ja" if int(plan.get("quick_check_return") or 0) else "Nein"),
            ("Rückgabe-Kommentar", plan.get("return_comment", "") or "-"),
            ("Erstellt", plan.get("created_at", "")),
            ("Ausgeliehen am", plan.get("checkout_at", "") or "-"),
            ("Zurückgegeben am", plan.get("returned_at", "") or "-"),
        ]

        for idx, (label, value) in enumerate(rows):
            ttk.Label(info, text=label).grid(row=idx, column=0, sticky="nw", padx=6, pady=2)
            ttk.Label(info, text=str(value), wraplength=620, justify="left").grid(row=idx, column=1, sticky="nw", padx=6, pady=2)

        sign_frame = ttk.LabelFrame(wrapper, text="Unterschriften")
        sign_frame.grid(row=1, column=0, sticky="nsew", pady=(10, 0))
        sign_frame.columnconfigure(0, weight=1)
        sign_frame.columnconfigure(1, weight=1)

        ttk.Label(sign_frame, text="Ausleihe").grid(row=0, column=0, sticky="w", padx=6, pady=4)
        ttk.Label(sign_frame, text="Rückgabe").grid(row=0, column=1, sticky="w", padx=6, pady=4)

        checkout_canvas = tk.Canvas(sign_frame, width=370, height=200, bg="white", highlightthickness=1, highlightbackground="#888")
        checkout_canvas.grid(row=1, column=0, padx=6, pady=6, sticky="nsew")
        return_canvas = tk.Canvas(sign_frame, width=370, height=200, bg="white", highlightthickness=1, highlightbackground="#888")
        return_canvas.grid(row=1, column=1, padx=6, pady=6, sticky="nsew")

        draw_signature_on_canvas(checkout_canvas, str(plan.get("signature_data") or ""))
        draw_signature_on_canvas(return_canvas, str(plan.get("return_signature_data") or ""))

        ttk.Button(wrapper, text="Schließen", command=self.destroy).grid(row=2, column=0, sticky="e", pady=(10, 0))


class ContactDialog(tk.Toplevel):
    def __init__(self, master: tk.Misc, *, item_label: str, von: str, rueck: str, initial: dict):
        super().__init__(master)
        self.title("Kontaktdaten ergänzen")
        self.geometry("560x280")
        self.resizable(False, False)
        self.transient(master)
        self.grab_set()

        self.result: dict | None = None

        info = f"Objekt: {item_label}\nZeitraum: {von} bis {rueck}"
        ttk.Label(self, text=info, justify="left", wraplength=540).pack(anchor="w", padx=10, pady=(10, 6))

        form = ttk.Frame(self)
        form.pack(fill="x", padx=10, pady=4)
        form.columnconfigure(1, weight=1)

        ttk.Label(form, text="Entleiher").grid(row=0, column=0, sticky="w", padx=(0, 6), pady=2)
        self.name_entry = ttk.Entry(form)
        self.name_entry.grid(row=0, column=1, sticky="ew", pady=2)
        self.name_entry.insert(0, (initial.get("entleiher") or "").strip())

        ttk.Label(form, text="E-Mail").grid(row=1, column=0, sticky="w", padx=(0, 6), pady=2)
        self.email_entry = ttk.Entry(form)
        self.email_entry.grid(row=1, column=1, sticky="ew", pady=2)
        self.email_entry.insert(0, (initial.get("entleiher_email") or "").strip())

        ttk.Label(form, text="Telefonnummer").grid(row=2, column=0, sticky="w", padx=(0, 6), pady=2)
        self.phone_entry = ttk.Entry(form)
        self.phone_entry.grid(row=2, column=1, sticky="ew", pady=2)
        self.phone_entry.insert(0, (initial.get("entleiher_telefon") or "").strip())

        ttk.Label(form, text="Adresse").grid(row=3, column=0, sticky="w", padx=(0, 6), pady=2)
        self.address_entry = ttk.Entry(form)
        self.address_entry.grid(row=3, column=1, sticky="ew", pady=2)
        self.address_entry.insert(0, (initial.get("entleiher_adresse") or "").strip())

        button_frame = ttk.Frame(self)
        button_frame.pack(fill="x", padx=10, pady=10)
        ttk.Button(button_frame, text="Abbrechen", command=self._cancel).pack(side=tk.RIGHT)
        ttk.Button(button_frame, text="Speichern", command=self._save).pack(side=tk.RIGHT, padx=(0, 6))

    def _save(self):
        self.result = {
            "entleiher": self.name_entry.get().strip(),
            "entleiher_email": self.email_entry.get().strip(),
            "entleiher_telefon": self.phone_entry.get().strip(),
            "entleiher_adresse": self.address_entry.get().strip(),
        }
        self.destroy()

    def _cancel(self):
        self.result = None
        self.destroy()


class VerleihApp(ttk.Frame):
    def __init__(self, master: tk.Tk, db_path: Path):
        super().__init__(master)
        self.db_path = db_path
        self.pack(fill=tk.BOTH, expand=True)

        self.selection_map: dict[str, tuple[str, int]] = {}
        self.signature_data = ""
        self.current_gal_target = ""
        self.gal_display_var = tk.StringVar(value="GAL: -")
        self.calendar_entries_for_month: list[dict] = []
        today = date.today()
        self.calendar_year = today.year
        self.calendar_month = today.month

        self._build_ui()
        self.refresh_data()

    def _validate_contact_for_lending(self, name: str, address: str, email: str, phone: str) -> bool:
        if not name:
            messagebox.showerror("Fehler", "Zum Ausleihen ist der Name des Entleihers erforderlich.")
            return False
        if not address:
            messagebox.showerror("Fehler", "Zum Ausleihen ist die Adresse erforderlich.")
            return False
        if not email and not phone:
            messagebox.showerror("Fehler", "Zum Ausleihen ist E-Mail oder Telefonnummer erforderlich.")
            return False
        return True

    def _build_ui(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        notebook = ttk.Notebook(self)
        notebook.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)

        plan_tab = ttk.Frame(notebook)
        search_tab = ttk.Frame(notebook)
        calendar_tab = ttk.Frame(notebook)
        notebook.add(plan_tab, text="Verleih planen")
        notebook.add(search_tab, text="Verfügbarkeit suchen")
        notebook.add(calendar_tab, text="Belegungs-Kalender")

        self._build_plan_tab(plan_tab)
        self._build_search_tab(search_tab)
        self._build_calendar_tab(calendar_tab)

        self.status_var = tk.StringVar(value="Bereit")
        ttk.Label(self, textvariable=self.status_var).grid(row=1, column=0, sticky="w", padx=10, pady=(0, 8))

    def _selected_item_labels(self) -> list[str]:
        if not hasattr(self, "item_listbox"):
            return []
        selected: list[str] = []
        for idx in self.item_listbox.curselection():
            selected.append(str(self.item_listbox.get(idx)))
        return selected

    def _selected_item_type_id(self) -> tuple[str, int] | None:
        labels = self._selected_item_labels()
        if not labels:
            return None
        first = labels[0]
        if first not in self.selection_map:
            return None
        return self.selection_map[first]

    def _update_selected_item_gal(self, _event=None):
        self.gal_provided_out_var.set(False)
        selected = self._selected_item_type_id()
        if not selected:
            self.current_gal_target = ""
            self.gal_display_var.set("GAL: -")
            self.open_gal_btn.configure(state=tk.DISABLED)
            return

        item_type, item_id = selected
        gal_file, gal_link = fetch_item_gal_data(self.db_path, item_type=item_type, item_id=item_id)

        if gal_link:
            self.current_gal_target = gal_link
            self.gal_display_var.set(f"GAL-Link: {gal_link}")
            self.open_gal_btn.configure(state=tk.NORMAL)
            return
        if gal_file:
            self.current_gal_target = gal_file
            self.gal_display_var.set(f"GAL-Datei: {gal_file}")
            self.open_gal_btn.configure(state=tk.NORMAL)
            return

        self.current_gal_target = ""
        self.gal_display_var.set("GAL: Kein Link/keine Datei hinterlegt")
        self.open_gal_btn.configure(state=tk.DISABLED)

    def _open_current_gal(self):
        open_reference(self.current_gal_target)

    def _build_plan_tab(self, parent: ttk.Frame):
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(2, weight=1)

        form = ttk.LabelFrame(parent, text="Neuen Verleih erfassen")
        form.grid(row=0, column=0, sticky="ew", padx=6, pady=6)
        form.columnconfigure(1, weight=1)

        self.create_mode_var = tk.StringVar(value="reserve")

        mode_frame = ttk.Frame(form)
        mode_frame.grid(row=0, column=0, columnspan=2, sticky="w", padx=6, pady=4)
        ttk.Radiobutton(
            mode_frame,
            text="Nur reservieren",
            variable=self.create_mode_var,
            value="reserve",
            command=self._update_mode_visibility,
        ).pack(side=tk.LEFT)
        ttk.Radiobutton(
            mode_frame,
            text="Direkt ausleihen",
            variable=self.create_mode_var,
            value="lend",
            command=self._update_mode_visibility,
        ).pack(side=tk.LEFT, padx=(12, 0))

        ttk.Label(form, text="Von (YYYY-MM-DD)").grid(row=1, column=0, sticky="w", padx=6, pady=2)
        self.von_entry = ttk.Entry(form)
        self.von_entry.grid(row=1, column=1, sticky="ew", padx=6, pady=2)

        ttk.Label(form, text="Bis / Rückgabe (YYYY-MM-DD)").grid(row=2, column=0, sticky="w", padx=6, pady=2)
        self.rueck_entry = ttk.Entry(form)
        self.rueck_entry.grid(row=2, column=1, sticky="ew", padx=6, pady=2)
        self.von_entry.insert(0, date.today().isoformat())
        self.rueck_entry.insert(0, date.today().isoformat())

        ttk.Label(form, text="Filter Produktbezeichnung (optional)").grid(row=3, column=0, sticky="w", padx=6, pady=2)
        self.item_filter_combo = ttk.Combobox(form, state="readonly")
        self.item_filter_combo.grid(row=3, column=1, sticky="ew", padx=6, pady=2)

        ttk.Button(form, text="Verfügbare Produkte/Systeme laden", command=self.load_available_items_for_period).grid(
            row=4, column=0, columnspan=2, sticky="ew", padx=6, pady=4
        )

        ttk.Label(form, text="Auswahl (nur verfügbare, Mehrfachwahl mit Strg/Shift)").grid(row=5, column=0, sticky="nw", padx=6, pady=2)
        list_container = ttk.Frame(form)
        list_container.grid(row=5, column=1, sticky="ew", padx=6, pady=2)
        list_container.columnconfigure(0, weight=1)
        self.item_listbox = tk.Listbox(list_container, selectmode=tk.EXTENDED, height=6, exportselection=False)
        self.item_listbox.grid(row=0, column=0, sticky="ew")
        item_scrollbar = ttk.Scrollbar(list_container, orient="vertical", command=self.item_listbox.yview)
        item_scrollbar.grid(row=0, column=1, sticky="ns")
        self.item_listbox.configure(yscrollcommand=item_scrollbar.set)
        self.item_listbox.bind("<<ListboxSelect>>", self._update_selected_item_gal)

        gal_frame = ttk.Frame(form)
        gal_frame.grid(row=6, column=0, columnspan=2, sticky="ew", padx=6, pady=(2, 4))
        gal_frame.columnconfigure(0, weight=1)
        ttk.Label(gal_frame, textvariable=self.gal_display_var, wraplength=580).grid(row=0, column=0, sticky="w")
        self.open_gal_btn = ttk.Button(gal_frame, text="GAL öffnen", command=self._open_current_gal, state=tk.DISABLED)
        self.open_gal_btn.grid(row=0, column=1, sticky="e", padx=(8, 0))

        ttk.Label(form, text="Ausgebende Person").grid(row=7, column=0, sticky="w", padx=6, pady=2)
        self.ausgebende_person_entry = ttk.Entry(form)
        self.ausgebende_person_entry.grid(row=7, column=1, sticky="ew", padx=6, pady=2)

        ttk.Label(form, text="Entleiher").grid(row=8, column=0, sticky="w", padx=6, pady=2)
        self.entleiher_entry = ttk.Entry(form)
        self.entleiher_entry.grid(row=8, column=1, sticky="ew", padx=6, pady=2)

        ttk.Label(form, text="E-Mail").grid(row=9, column=0, sticky="w", padx=6, pady=2)
        self.email_entry = ttk.Entry(form)
        self.email_entry.grid(row=9, column=1, sticky="ew", padx=6, pady=2)

        ttk.Label(form, text="Telefonnummer").grid(row=10, column=0, sticky="w", padx=6, pady=2)
        self.phone_entry = ttk.Entry(form)
        self.phone_entry.grid(row=10, column=1, sticky="ew", padx=6, pady=2)

        ttk.Label(form, text="Adresse").grid(row=11, column=0, sticky="w", padx=6, pady=2)
        self.address_entry = ttk.Entry(form)
        self.address_entry.grid(row=11, column=1, sticky="ew", padx=6, pady=2)

        self.quick_check_out_var = tk.BooleanVar(value=False)
        self.quick_check_out_cb = ttk.Checkbutton(form, text="Kurzkontrolle durchgeführt", variable=self.quick_check_out_var)
        self.quick_check_out_cb.grid(
            row=12, column=0, columnspan=2, sticky="w", padx=6, pady=2
        )

        self.gal_provided_out_var = tk.BooleanVar(value=False)
        self.gal_provided_out_cb = ttk.Checkbutton(form, text="GAL bereitgestellt", variable=self.gal_provided_out_var)
        self.gal_provided_out_cb.grid(
            row=13, column=0, columnspan=2, sticky="w", padx=6, pady=2
        )

        self.sig_frame = ttk.Frame(form)
        self.sig_frame.grid(row=14, column=0, columnspan=2, sticky="ew", padx=6, pady=6)
        self.sig_frame.columnconfigure(1, weight=1)
        ttk.Button(self.sig_frame, text="Digitale Unterschrift", command=self.capture_signature).grid(row=0, column=0, sticky="w")
        self.sig_status_var = tk.StringVar(value="Keine Unterschrift erfasst")
        ttk.Label(self.sig_frame, textvariable=self.sig_status_var).grid(row=0, column=1, sticky="w", padx=8)

        action_frame = ttk.Frame(form)
        action_frame.grid(row=15, column=0, columnspan=2, sticky="ew", padx=6, pady=(0, 8))
        action_frame.columnconfigure(0, weight=1)
        action_frame.columnconfigure(1, weight=1)
        action_frame.columnconfigure(2, weight=1)
        ttk.Button(action_frame, text="Speichern", command=self.save_verleih).grid(row=0, column=0, sticky="ew", padx=(0, 4))
        ttk.Button(action_frame, text="Leeren", command=self.clear_verleih_form).grid(row=0, column=1, sticky="ew", padx=4)
        ttk.Button(action_frame, text="Aktualisieren", command=self.refresh_data).grid(row=0, column=2, sticky="ew", padx=(4, 0))

        list_frame = ttk.LabelFrame(parent, text="Anstehende Verleihungen / Rückgaben")
        list_frame.grid(row=2, column=0, sticky="nsew", padx=6, pady=6)
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)

        self.plan_tree = ttk.Treeview(
            list_frame,
            columns=("item", "von", "rueck", "entleiher", "status", "due"),
            show="headings",
            height=10,
        )
        self.plan_tree.heading("item", text="Bezeichnung")
        self.plan_tree.heading("von", text="Ausleihdatum")
        self.plan_tree.heading("rueck", text="Rückgabedatum")
        self.plan_tree.heading("entleiher", text="Entleiher")
        self.plan_tree.heading("status", text="Status")
        self.plan_tree.heading("due", text="Rückgabe-Hinweis")

        self.plan_tree.column("item", width=330, anchor="w")
        self.plan_tree.column("von", width=130, anchor="center")
        self.plan_tree.column("rueck", width=130, anchor="center")
        self.plan_tree.column("entleiher", width=150, anchor="w")
        self.plan_tree.column("status", width=120, anchor="center")
        self.plan_tree.column("due", width=180, anchor="center")
        self.plan_tree.grid(row=0, column=0, sticky="nsew")
        self.plan_tree.bind("<Double-1>", self.open_selected_overview)

        self.plan_tree.tag_configure("overdue", foreground="#b00020")
        self.plan_tree.tag_configure("reserved", foreground="#333399")
        self.plan_tree.tag_configure("lent", foreground="#006400")

        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=self.plan_tree.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.plan_tree.configure(yscrollcommand=scrollbar.set)

        table_action_frame = ttk.Frame(parent)
        table_action_frame.grid(row=3, column=0, sticky="ew", padx=6, pady=(0, 6))
        table_action_frame.columnconfigure(0, weight=1)
        table_action_frame.columnconfigure(1, weight=1)
        table_action_frame.columnconfigure(2, weight=1)
        ttk.Button(table_action_frame, text="Reservierung ausleihen", command=self.checkout_selected_reservation).grid(
            row=0, column=0, sticky="ew", padx=(0, 4)
        )
        ttk.Button(table_action_frame, text="Kontaktdaten ergänzen", command=self.edit_selected_contact).grid(
            row=0, column=1, sticky="ew", padx=4
        )
        ttk.Button(table_action_frame, text="Rückgabe bestätigen", command=self.confirm_selected_return).grid(
            row=0, column=2, sticky="ew", padx=(4, 0)
        )

        self._update_mode_visibility()

    def _update_mode_visibility(self):
        is_lend_mode = self.create_mode_var.get() == "lend"

        if is_lend_mode:
            self.von_entry.delete(0, tk.END)
            self.von_entry.insert(0, date.today().isoformat())
            self.rueck_entry.delete(0, tk.END)
            self.rueck_entry.insert(0, date.today().isoformat())
            self.quick_check_out_cb.grid()
            self.gal_provided_out_cb.grid()
            self.sig_frame.grid()
        else:
            self.quick_check_out_cb.grid_remove()
            self.gal_provided_out_cb.grid_remove()
            self.sig_frame.grid_remove()
            self.quick_check_out_var.set(False)
            self.gal_provided_out_var.set(False)
            self.signature_data = ""
            self.sig_status_var.set("Keine Unterschrift erfasst")

    def clear_verleih_form(self):
        self.von_entry.delete(0, tk.END)
        self.von_entry.insert(0, date.today().isoformat())
        self.rueck_entry.delete(0, tk.END)
        self.rueck_entry.insert(0, date.today().isoformat())
        self.ausgebende_person_entry.delete(0, tk.END)
        self.entleiher_entry.delete(0, tk.END)
        self.email_entry.delete(0, tk.END)
        self.phone_entry.delete(0, tk.END)
        self.address_entry.delete(0, tk.END)
        self.item_filter_combo.set("")
        self.selection_map.clear()
        self.item_listbox.delete(0, tk.END)
        self.signature_data = ""
        self.quick_check_out_var.set(False)
        self.gal_provided_out_var.set(False)
        self.sig_status_var.set("Keine Unterschrift erfasst")
        self.current_gal_target = ""
        self.gal_display_var.set("GAL: -")
        self.open_gal_btn.configure(state=tk.DISABLED)

    def _build_calendar_tab(self, parent: ttk.Frame):
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(2, weight=1)

        head = ttk.Frame(parent)
        head.grid(row=0, column=0, sticky="ew", padx=6, pady=(6, 4))
        head.columnconfigure(1, weight=1)

        ttk.Button(head, text="◀ Monat", command=lambda: self._shift_calendar_month(-1)).grid(row=0, column=0, sticky="w")
        self.calendar_title_var = tk.StringVar(value="")
        ttk.Label(head, textvariable=self.calendar_title_var, anchor="center").grid(row=0, column=1, sticky="ew")
        ttk.Button(head, text="Monat ▶", command=lambda: self._shift_calendar_month(1)).grid(row=0, column=2, sticky="e")

        grid_frame = ttk.LabelFrame(parent, text="Monatsübersicht (aktive Reservierungen/Ausleihen)")
        grid_frame.grid(row=1, column=0, sticky="ew", padx=6, pady=(0, 6))
        for c in range(7):
            grid_frame.columnconfigure(c, weight=1)

        for col, title in enumerate(["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]):
            ttk.Label(grid_frame, text=title, anchor="center").grid(row=0, column=col, sticky="ew", padx=2, pady=(2, 4))

        self.calendar_day_buttons: list[ttk.Button] = []
        for row in range(1, 7):
            for col in range(7):
                btn = ttk.Button(grid_frame, text="", command=lambda: None)
                btn.grid(row=row, column=col, sticky="nsew", padx=2, pady=2)
                self.calendar_day_buttons.append(btn)

        day_frame = ttk.LabelFrame(parent, text="Belegung am ausgewählten Tag")
        day_frame.grid(row=2, column=0, sticky="nsew", padx=6, pady=(0, 6))
        day_frame.columnconfigure(0, weight=1)
        day_frame.rowconfigure(1, weight=1)

        self.calendar_day_var = tk.StringVar(value="Tag auswählen")
        ttk.Label(day_frame, textvariable=self.calendar_day_var).grid(row=0, column=0, sticky="w", padx=6, pady=(6, 4))

        self.calendar_tree = ttk.Treeview(
            day_frame,
            columns=("item", "status", "von", "bis", "entleiher"),
            show="headings",
            height=10,
        )
        self.calendar_tree.heading("item", text="Bezeichnung")
        self.calendar_tree.heading("status", text="Status")
        self.calendar_tree.heading("von", text="Von")
        self.calendar_tree.heading("bis", text="Bis")
        self.calendar_tree.heading("entleiher", text="Entleiher")
        self.calendar_tree.column("item", width=380, anchor="w")
        self.calendar_tree.column("status", width=110, anchor="center")
        self.calendar_tree.column("von", width=110, anchor="center")
        self.calendar_tree.column("bis", width=110, anchor="center")
        self.calendar_tree.column("entleiher", width=180, anchor="w")
        self.calendar_tree.grid(row=1, column=0, sticky="nsew", padx=6, pady=(0, 6))

        cal_scroll = ttk.Scrollbar(day_frame, orient="vertical", command=self.calendar_tree.yview)
        cal_scroll.grid(row=1, column=1, sticky="ns", pady=(0, 6))
        self.calendar_tree.configure(yscrollcommand=cal_scroll.set)

    def _shift_calendar_month(self, delta: int):
        month = self.calendar_month + delta
        year = self.calendar_year
        if month < 1:
            month = 12
            year -= 1
        elif month > 12:
            month = 1
            year += 1
        self.calendar_month = month
        self.calendar_year = year
        self._refresh_calendar()

    def _refresh_calendar(self):
        month_name = calendar.month_name[self.calendar_month]
        self.calendar_title_var.set(f"{month_name} {self.calendar_year}")

        first_weekday, days_in_month = calendar.monthrange(self.calendar_year, self.calendar_month)
        month_start = date(self.calendar_year, self.calendar_month, 1)
        month_end = date(self.calendar_year, self.calendar_month, days_in_month)

        self.calendar_entries_for_month = fetch_calendar_entries(
            self.db_path,
            from_date=month_start.isoformat(),
            to_date=month_end.isoformat(),
        )

        counts: dict[int, int] = {d: 0 for d in range(1, days_in_month + 1)}
        for entry in self.calendar_entries_for_month:
            try:
                start_d = parse_date(str(entry.get("von_datum") or "")).date()
                end_d = parse_date(str(entry.get("rueckgabe_datum") or "")).date()
            except ValueError:
                continue
            start_d = max(start_d, month_start)
            end_d = min(end_d, month_end)
            cur = start_d
            while cur <= end_d:
                counts[cur.day] = counts.get(cur.day, 0) + 1
                cur += timedelta(days=1)

        for btn in self.calendar_day_buttons:
            btn.configure(text="", state=tk.DISABLED, command=lambda: None)

        start_idx = first_weekday
        for day in range(1, days_in_month + 1):
            btn = self.calendar_day_buttons[start_idx + day - 1]
            count = counts.get(day, 0)
            label = f"{day}\n({count})" if count > 0 else str(day)
            btn.configure(text=label, state=tk.NORMAL, command=lambda d=day: self._show_calendar_day(d))

        if date.today().year == self.calendar_year and date.today().month == self.calendar_month:
            self._show_calendar_day(date.today().day)
        else:
            self._show_calendar_day(1)

    def _show_calendar_day(self, day: int):
        day_date = date(self.calendar_year, self.calendar_month, day)
        self.calendar_day_var.set(f"Tag: {day_date.isoformat()}")

        for row in self.calendar_tree.get_children():
            self.calendar_tree.delete(row)

        for entry in self.calendar_entries_for_month:
            try:
                start_d = parse_date(str(entry.get("von_datum") or "")).date()
                end_d = parse_date(str(entry.get("rueckgabe_datum") or "")).date()
            except ValueError:
                continue
            if not (start_d <= day_date <= end_d):
                continue
            status = (entry.get("status") or "").strip().lower()
            status_label = "Reserviert" if status == "reserved" else "Ausgeliehen"
            self.calendar_tree.insert(
                "",
                tk.END,
                values=(
                    entry.get("item_label", ""),
                    status_label,
                    entry.get("von_datum", ""),
                    entry.get("rueckgabe_datum", ""),
                    entry.get("entleiher", ""),
                ),
            )

    def _build_search_tab(self, parent: ttk.Frame):
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(1, weight=1)

        search_form = ttk.LabelFrame(parent, text="Freie Produkte/Systeme suchen")
        search_form.grid(row=0, column=0, sticky="ew", padx=6, pady=6)
        search_form.columnconfigure(1, weight=1)

        ttk.Label(search_form, text="Bezeichnung / Name / SN").grid(row=0, column=0, sticky="w", padx=6, pady=2)
        self.search_bez_entry = ttk.Entry(search_form)
        self.search_bez_entry.grid(row=0, column=1, sticky="ew", padx=6, pady=2)

        ttk.Label(search_form, text="Von (YYYY-MM-DD)").grid(row=1, column=0, sticky="w", padx=6, pady=2)
        self.search_von_entry = ttk.Entry(search_form)
        self.search_von_entry.grid(row=1, column=1, sticky="ew", padx=6, pady=2)

        ttk.Label(search_form, text="Bis (YYYY-MM-DD)").grid(row=2, column=0, sticky="w", padx=6, pady=2)
        self.search_bis_entry = ttk.Entry(search_form)
        self.search_bis_entry.grid(row=2, column=1, sticky="ew", padx=6, pady=2)

        ttk.Button(search_form, text="Suchen", command=self.search_available_products).grid(
            row=3, column=0, columnspan=2, sticky="ew", padx=6, pady=6
        )

        result_frame = ttk.LabelFrame(parent, text="Verfügbare Produkte/Systeme im Zeitraum")
        result_frame.grid(row=1, column=0, sticky="nsew", padx=6, pady=6)
        result_frame.columnconfigure(0, weight=1)
        result_frame.rowconfigure(0, weight=1)

        self.search_tree = ttk.Treeview(
            result_frame,
            columns=("label",),
            show="headings",
            height=14,
        )
        self.search_tree.heading("label", text="Bezeichnung")
        self.search_tree.column("label", width=760, anchor="w")
        self.search_tree.grid(row=0, column=0, sticky="nsew")

        scrollbar = ttk.Scrollbar(result_frame, orient="vertical", command=self.search_tree.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.search_tree.configure(yscrollcommand=scrollbar.set)

    def _refresh_item_combobox(self, items: list[dict] | None = None):
        self.selection_map.clear()
        labels = []

        if items is None:
            items = []

        for item in items:
            label = item.get("item_label", "")
            item_type = str(item.get("item_type", ""))
            item_id = int(item.get("item_id", 0))
            if not label or not item_type or item_id <= 0:
                continue
            self.selection_map[label] = (item_type, item_id)
            labels.append(label)

        self.item_listbox.delete(0, tk.END)
        for label in labels:
            self.item_listbox.insert(tk.END, label)
        if labels:
            self.item_listbox.selection_set(0)
        self._update_selected_item_gal()

    def refresh_data(self):
        filter_values = [""] + fetch_distinct_produktbezeichnungen(self.db_path)
        self.item_filter_combo["values"] = filter_values
        self.item_filter_combo.set("")
        self._refresh_item_combobox([])
        self._refresh_plan_list()
        self._refresh_calendar()
        self.current_gal_target = ""
        self.gal_display_var.set("GAL: -")
        self.open_gal_btn.configure(state=tk.DISABLED)
        self.status_var.set("Daten aktualisiert")

    def load_available_items_for_period(self):
        von = self.von_entry.get().strip()
        bis = self.rueck_entry.get().strip()
        suchtext = self.item_filter_combo.get().strip()

        if self.create_mode_var.get() == "lend":
            von = date.today().isoformat()
            self.von_entry.delete(0, tk.END)
            self.von_entry.insert(0, von)

        try:
            von_dt = parse_date(von)
            bis_dt = parse_date(bis)
            if bis_dt < von_dt:
                messagebox.showerror("Fehler", "Bis/Rückgabe muss gleich oder nach Von liegen.")
                return
        except ValueError:
            messagebox.showerror("Fehler", "Datum bitte im Format YYYY-MM-DD eingeben.")
            return

        items = find_available_items(self.db_path, suchtext, von, bis)
        self._refresh_item_combobox(items)
        if not items:
            messagebox.showwarning("Keine Treffer", "Im gewählten Zeitraum sind keine passenden Produkte/Systeme verfügbar.")
        self.status_var.set(f"{len(items)} verfügbare Produkte/Systeme geladen")

    def _refresh_plan_list(self):
        today = datetime.now().date()
        for row in self.plan_tree.get_children():
            self.plan_tree.delete(row)

        for plan in fetch_pending_verleihplaene(self.db_path):
            plan_id = int(plan.get("id", 0))
            status = (plan.get("status") or "").strip().lower() or "lent"
            rueckgabe_raw = plan.get("rueckgabe_datum", "")

            due_hint = ""
            tag = status
            try:
                rueckgabe_date = parse_date(rueckgabe_raw).date()
                days = (rueckgabe_date - today).days
                if status == "lent":
                    if days < 0:
                        due_hint = f"Überfällig seit {-days} Tag(en)"
                        tag = "overdue"
                    elif days == 0:
                        due_hint = "Heute fällig"
                    else:
                        due_hint = f"In {days} Tag(en) fällig"
                else:
                    due_hint = f"Geplant, Rückgabe in {max(days, 0)} Tag(en)"
            except ValueError:
                due_hint = "Ungültiges Datum"

            status_label = "Reserviert" if status == "reserved" else "Ausgeliehen"
            self.plan_tree.insert(
                "",
                tk.END,
                iid=str(plan_id),
                values=(
                    plan.get("item_label", ""),
                    plan.get("von_datum", ""),
                    plan.get("rueckgabe_datum", ""),
                    plan.get("entleiher", ""),
                    status_label,
                    due_hint,
                ),
                tags=(tag,),
            )

    def _selected_plan_id(self) -> int | None:
        selected = self.plan_tree.selection()
        if not selected:
            return None
        try:
            return int(selected[0])
        except ValueError:
            return None

    def open_selected_overview(self, _event=None):
        plan_id = self._selected_plan_id()
        if plan_id is None:
            return
        plan = fetch_verleih_by_id(self.db_path, plan_id)
        if not plan:
            messagebox.showerror("Fehler", "Eintrag wurde nicht gefunden.")
            return
        VerleihOverviewDialog(self, plan)

    def capture_signature(self):
        selected_labels = self._selected_item_labels()
        von = self.von_entry.get().strip()
        rueck = self.rueck_entry.get().strip()
        details = ""
        if selected_labels:
            if len(selected_labels) == 1:
                details += f"Objekt: {selected_labels[0]}\n"
            else:
                details += f"Objekte: {len(selected_labels)} ausgewählt\n"
        if von and rueck:
            details += f"Zeitraum: {von} bis {rueck}"

        dialog = SignatureDialog(self, details)
        self.wait_window(dialog)
        if dialog.result:
            self.signature_data = dialog.result
            self.sig_status_var.set("Unterschrift erfasst")

    def save_verleih(self):
        selected_labels = [label for label in self._selected_item_labels() if label in self.selection_map]
        if not selected_labels:
            messagebox.showerror("Fehler", "Bitte mindestens ein Produkt oder System auswählen.")
            return

        is_lend_mode = self.create_mode_var.get() == "lend"
        von = self.von_entry.get().strip()
        rueck = self.rueck_entry.get().strip()
        entleiher = self.entleiher_entry.get().strip()
        ausgebende_person = self.ausgebende_person_entry.get().strip()
        email = self.email_entry.get().strip()
        telefon = self.phone_entry.get().strip()
        adresse = self.address_entry.get().strip()

        try:
            von_dt = parse_date(von)
            rueck_dt = parse_date(rueck)
            if rueck_dt < von_dt:
                messagebox.showerror("Fehler", "Rückgabedatum muss nach dem Ausleihdatum liegen.")
                return
        except ValueError:
            messagebox.showerror("Fehler", "Datum bitte im Format YYYY-MM-DD eingeben.")
            return

        if not self.selection_map:
            messagebox.showerror("Fehler", "Bitte zuerst den Zeitraum eingeben und verfügbare Produkte/Systeme laden.")
            return

        status = "reserved"
        checkout_at = None
        signature_data = ""

        for selection_label in selected_labels:
            item_type, item_id = self.selection_map[selection_label]

            next_pruefung = fetch_item_next_pruefung_am(self.db_path, item_type=item_type, item_id=item_id)
            if not next_pruefung:
                messagebox.showerror("PSA-Prüfung", f"{selection_label}\nKeine nächste Prüfung hinterlegt.")
                return
            try:
                next_pruefung_dt = parse_date(next_pruefung)
            except ValueError:
                messagebox.showerror("PSA-Prüfung", f"{selection_label}\nUngültiges Datum bei 'Nächste Prüfung': {next_pruefung}")
                return
            if rueck_dt > next_pruefung_dt:
                messagebox.showerror(
                    "PSA-Prüfung",
                    f"{selection_label}\nRückgabedatum ({rueck}) liegt nach der Frist zur nächsten Prüfung ({next_pruefung}).",
                )
                return

            if not is_item_available(
                self.db_path,
                item_type=item_type,
                item_id=item_id,
                von_datum=von,
                bis_datum=rueck,
            ):
                if not is_item_psa_current(self.db_path, item_type=item_type, item_id=item_id):
                    messagebox.showerror("PSA-Prüfung", f"{selection_label}\nhat keine aktuelle PSA-Prüfung und kann nicht ausgeliehen/reserviert werden.")
                elif is_item_locked(self.db_path, item_type=item_type, item_id=item_id):
                    messagebox.showerror("Gesperrt", f"{selection_label}\nist gesperrt und kann nicht reserviert oder ausgeliehen werden.")
                else:
                    messagebox.showerror("Nicht verfügbar", f"{selection_label}\nist im Zeitraum bereits vergeben.")
                return

        if is_lend_mode:
            if not self._validate_contact_for_lending(entleiher, adresse, email, telefon):
                return
            if not ausgebende_person:
                messagebox.showerror("Fehler", "Bitte die ausgebende Person eintragen.")
                return
            if not self.quick_check_out_var.get():
                messagebox.showerror("Fehler", "Bitte Kurzkontrolle für die Ausleihe bestätigen.")
                return
            if not self.gal_provided_out_var.get():
                messagebox.showerror("Fehler", "Bitte GAL bereitgestellt bestätigen.")
                return
            for selection_label in selected_labels:
                item_type, item_id = self.selection_map[selection_label]
                gal_file, gal_link = fetch_item_gal_data(self.db_path, item_type=item_type, item_id=item_id)
                if not (gal_file or gal_link):
                    messagebox.showerror("Fehler", f"Für {selection_label} ist kein GAL-Link und keine GAL-Datei hinterlegt.")
                    return
            if not self.signature_data:
                messagebox.showerror("Fehler", "Für direktes Ausleihen wird eine Unterschrift benötigt.")
                return
            status = "lent"
            checkout_at = datetime.now().isoformat(timespec="seconds")
            signature_data = self.signature_data

        created_ids: list[int] = []
        for selection_label in selected_labels:
            item_type, item_id = self.selection_map[selection_label]
            new_id = insert_verleih(
                self.db_path,
                item_type=item_type,
                item_id=item_id,
                item_label=selection_label,
                von_datum=von,
                rueckgabe_datum=rueck,
                entleiher=entleiher,
                ausgebende_person=ausgebende_person,
                entleiher_email=email,
                entleiher_telefon=telefon,
                entleiher_adresse=adresse,
                signature_data=signature_data,
                quick_check_out=self.quick_check_out_var.get() if is_lend_mode else False,
                gal_provided_out=self.gal_provided_out_var.get() if is_lend_mode else False,
                status=status,
                checkout_at=checkout_at,
            )
            created_ids.append(new_id)

        self.status_var.set(f"{len(created_ids)} Eintrag/Einträge gespeichert")
        self._refresh_plan_list()
        self._refresh_calendar()

    def edit_selected_contact(self):
        plan_id = self._selected_plan_id()
        if plan_id is None:
            messagebox.showerror("Fehler", "Bitte einen Eintrag in der Tabelle auswählen.")
            return

        plan = fetch_verleih_by_id(self.db_path, plan_id)
        if not plan:
            messagebox.showerror("Fehler", "Eintrag wurde nicht gefunden.")
            return

        dialog = ContactDialog(
            self,
            item_label=plan.get("item_label", ""),
            von=plan.get("von_datum", ""),
            rueck=plan.get("rueckgabe_datum", ""),
            initial=plan,
        )
        self.wait_window(dialog)
        if not dialog.result:
            return

        update_verleih_contact(
            self.db_path,
            plan_id,
            entleiher=dialog.result.get("entleiher", ""),
            entleiher_email=dialog.result.get("entleiher_email", ""),
            entleiher_telefon=dialog.result.get("entleiher_telefon", ""),
            entleiher_adresse=dialog.result.get("entleiher_adresse", ""),
        )
        self.status_var.set(f"Kontaktdaten für Eintrag {plan_id} aktualisiert")
        self._refresh_plan_list()
        self._refresh_calendar()

    def checkout_selected_reservation(self):
        plan_id = self._selected_plan_id()
        if plan_id is None:
            messagebox.showerror("Fehler", "Bitte einen Eintrag in der Tabelle auswählen.")
            return

        plan = fetch_verleih_by_id(self.db_path, plan_id)
        if not plan:
            messagebox.showerror("Fehler", "Eintrag wurde nicht gefunden.")
            return
        if (plan.get("status") or "").strip().lower() != "reserved":
            messagebox.showinfo("Hinweis", "Nur Reservierungen können ausgeliehen werden.")
            return

        if not is_item_psa_current(
            self.db_path,
            item_type=str(plan.get("item_type") or ""),
            item_id=int(plan.get("item_id") or 0),
        ):
            messagebox.showerror("PSA-Prüfung", "Dieses Produkt/System hat keine aktuelle PSA-Prüfung und kann nicht ausgeliehen werden.")
            return

        item_type_for_check = str(plan.get("item_type") or "")
        item_id_for_check = int(plan.get("item_id") or 0)
        rueckgabe_plan = str(plan.get("rueckgabe_datum") or "").strip()
        next_pruefung = fetch_item_next_pruefung_am(self.db_path, item_type=item_type_for_check, item_id=item_id_for_check)
        if not next_pruefung:
            messagebox.showerror("PSA-Prüfung", "Keine nächste Prüfung hinterlegt. Ausleihe nicht möglich.")
            return
        try:
            if parse_date(rueckgabe_plan) > parse_date(next_pruefung):
                messagebox.showerror(
                    "PSA-Prüfung",
                    f"Rückgabedatum ({rueckgabe_plan}) liegt nach der Frist zur nächsten Prüfung ({next_pruefung}).",
                )
                return
        except ValueError:
            messagebox.showerror("PSA-Prüfung", "Datumsangaben zur Prüfung sind ungültig. Ausleihe nicht möglich.")
            return

        if not self._validate_contact_for_lending(
            (plan.get("entleiher") or "").strip(),
            (plan.get("entleiher_adresse") or "").strip(),
            (plan.get("entleiher_email") or "").strip(),
            (plan.get("entleiher_telefon") or "").strip(),
        ):
            messagebox.showerror(
                "Fehlende Entleiherdaten",
                "Für diese Reservierung fehlen Pflichtdaten (Name, Adresse und E-Mail oder Telefon).",
            )
            return

        details = (
            f"Objekt: {plan.get('item_label', '')}\n"
            f"Zeitraum: {plan.get('von_datum', '')} bis {plan.get('rueckgabe_datum', '')}"
        )

        item_type = str(plan.get("item_type") or "")
        item_id = int(plan.get("item_id") or 0)
        gal_file, gal_link = fetch_item_gal_data(self.db_path, item_type=item_type, item_id=item_id)
        gal_target = gal_link or gal_file
        gal_display = gal_link if gal_link else gal_file

        dialog = CheckoutDialog(self, details, gal_display=gal_display, gal_target=gal_target)
        self.wait_window(dialog)
        if not dialog.result:
            return

        checkout_reserved_verleih(
            self.db_path,
            plan_id,
            dialog.result.get("signature", ""),
            bool(dialog.result.get("quick_check_out", False)),
            bool(dialog.result.get("gal_provided_out", False)),
            dialog.result.get("ausgebende_person", ""),
        )
        self.status_var.set(f"Reservierung {plan_id} wurde ausgeliehen")
        self._refresh_plan_list()
        self._refresh_calendar()

    def confirm_selected_return(self):
        plan_id = self._selected_plan_id()
        if plan_id is None:
            messagebox.showerror("Fehler", "Bitte einen Eintrag in der Tabelle auswählen.")
            return

        plan = fetch_verleih_by_id(self.db_path, plan_id)
        if not plan:
            messagebox.showerror("Fehler", "Eintrag wurde nicht gefunden.")
            return
        if (plan.get("status") or "").strip().lower() != "lent":
            messagebox.showinfo("Hinweis", "Rückgabe kann nur für ausgeliehene Einträge bestätigt werden.")
            return

        details = (
            f"Objekt: {plan.get('item_label', '')}\n"
            f"Zeitraum: {plan.get('von_datum', '')} bis {plan.get('rueckgabe_datum', '')}"
        )
        dialog = ReturnDialog(self, details)
        self.wait_window(dialog)
        if not dialog.result:
            return

        confirm_return(
            self.db_path,
            plan_id,
            return_comment=dialog.result.get("comment", ""),
            ruecknehmende_person=dialog.result.get("ruecknehmende_person", ""),
            return_signature_data=dialog.result.get("signature", ""),
            quick_check_return=bool(dialog.result.get("quick_check_return", False)),
        )

        if bool(dialog.result.get("lock_item", False)):
            lock_item(
                self.db_path,
                item_type=str(plan.get("item_type") or ""),
                item_id=int(plan.get("item_id") or 0),
                lock_comment=dialog.result.get("lock_comment", ""),
            )
        self.status_var.set(f"Rückgabe für Eintrag {plan_id} bestätigt")
        self._refresh_plan_list()
        self._refresh_calendar()

    def search_available_products(self):
        suchtext = self.search_bez_entry.get().strip()
        von = self.search_von_entry.get().strip()
        bis = self.search_bis_entry.get().strip()

        try:
            von_dt = parse_date(von)
            bis_dt = parse_date(bis)
            if bis_dt < von_dt:
                messagebox.showerror("Fehler", "Bis-Datum muss gleich oder nach Von-Datum liegen.")
                return
        except ValueError:
            messagebox.showerror("Fehler", "Datumsformat: YYYY-MM-DD")
            return

        results = find_available_items(self.db_path, suchtext, von, bis)

        for row in self.search_tree.get_children():
            self.search_tree.delete(row)

        for item in results:
            self.search_tree.insert(
                "",
                tk.END,
                values=(item.get("item_label", ""),),
            )

        self.status_var.set(f"{len(results)} verfügbare Produkte/Systeme gefunden")


def main():
    init_db(DEFAULT_DB_PATH)
    ensure_verleih_schema(DEFAULT_DB_PATH)

    root = tk.Tk()
    root.title("PSA Verleihplanung")
    root.geometry("1050x760")

    VerleihApp(root, DEFAULT_DB_PATH)
    root.mainloop()


if __name__ == "__main__":
    main()
