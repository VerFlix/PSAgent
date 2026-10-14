"""Schema-Varianten der Altsystem-Datenbanken `psa.db` und `psa_archiv.db`.

Das Altsystem (`psa_gui.py`, `verleih_gui.py`, `generate_PDF.py`) legt seine Tabellen beim
Programmstart an und rüstet fehlende Spalten per `ALTER TABLE` nach. Nachgerüstete Spalten
landen am Ende der Tabelle. Je nachdem, mit welcher Programmversion eine Datenbank angelegt
und mit welcher sie zuletzt geöffnet wurde, unterscheiden sich Tabellen, Spalten und
Spaltenreihenfolge.

Schema-Stände (Commit, in dem der Stand entstand):

- S1 `93ec23f`: products, systems, system_parts
- S2 `0480fd0`: item_locks, verleih_planung
- S3 `59d5b3c`: Prüfberichte, nächste/letzte PSA-Prüfung, gal_provided_out
  (`34509a1` legt dieselben Tabellen nur in anderer Reihenfolge an)
- S4 `105fc0f`: ausgebende_person, ruecknehmende_person
- S5 `59ec978`: Archivdatenbank mit archived_items
- S6 `176ac49`: last_psa_durchgefallen_am
- S7 `b441ab6`: keine_psa (aktueller Stand, unverändert bis `4ba05f4`)

Varianten:

- `sN`: frisch mit Stand SN angelegt (PSA- und Verleih-Programm einmal gestartet)
- `sN_aktualisiert`: Stand SN, danach mit dem aktuellen PSA-Programm geöffnet
- `sN_nur_verleih`: Stand SN, danach nur mit dem aktuellen Verleih-Programm geöffnet

Die Archivdatenbank entsteht erst beim ersten Archivieren und wird nur dabei nachgerüstet. In
den nachgerüsteten Varianten bleibt sie deshalb auf dem alten Stand. Nicht aufgeführte
Kombinationen ergeben kein neues Schema: `s1_aktualisiert` entspricht `s7` ohne Archiv,
`s4_*` entspricht `s3_*`, `s6_nur_verleih` entspricht `s6_aktualisiert`.

Die DDL unten ist das Ergebnis des echten historischen Codes. `tests/legacy` führt bei jedem
Testlauf die Schema-Funktionen der genannten Altsystem-Version aus und vergleicht das Ergebnis
mit diesen Varianten.
"""

import sqlite3
from dataclasses import dataclass
from enum import StrEnum

# Stand des Altsystems im Repo (psa_gui.py, verleih_gui.py, generate_PDF.py).
CURRENT_LEGACY_COMMIT = "4ba05f4"


class Upgrade(StrEnum):
    """Mit welchem aktuellen Programm eine alte Datenbank zuletzt geöffnet wurde."""

    PSA = "psa"
    VERLEIH = "verleih"


@dataclass(frozen=True)
class SchemaVariant:
    """Eine Schema-Variante: Tabellen der Haupt- und (optional) der Archivdatenbank."""

    name: str
    description: str
    base_commit: str
    upgrade: Upgrade | None
    main: tuple[str, ...]
    archive: tuple[str, ...] = ()

    @property
    def has_archive(self) -> bool:
        """True, wenn zur Variante eine Archivdatenbank gehört."""
        return bool(self.archive)


@dataclass(frozen=True)
class ColumnInfo:
    """Eine Spalte, wie `PRAGMA table_info` sie beschreibt."""

    name: str
    type: str
    notnull: bool
    default: str | None
    primary_key: bool


@dataclass(frozen=True)
class TableInfo:
    """Aufbau einer Tabelle: Spalten in Reihenfolge, eindeutige Indizes, Fremdschlüssel."""

    columns: tuple[ColumnInfo, ...]
    unique_indexes: tuple[tuple[str, ...], ...]
    foreign_keys: tuple[tuple[str, str, str], ...]

    @property
    def column_names(self) -> tuple[str, ...]:
        """Spaltennamen in Tabellenreihenfolge."""
        return tuple(column.name for column in self.columns)


def create_tables(conn: sqlite3.Connection, statements: tuple[str, ...]) -> None:
    """Legt die Tabellen einer Variante in der angegebenen Reihenfolge an."""
    for statement in statements:
        conn.execute(statement)


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def describe_schema(conn: sqlite3.Connection) -> dict[str, TableInfo]:
    """Beschreibt alle Tabellen einer SQLite-Datenbank (ohne interne `sqlite_*`-Tabellen)."""
    tables = [
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' "
            "ORDER BY name"
        )
    ]
    result: dict[str, TableInfo] = {}
    for table in tables:
        quoted = _quote(table)
        columns = tuple(
            ColumnInfo(
                name=row[1],
                type=row[2],
                notnull=bool(row[3]),
                default=row[4],
                primary_key=bool(row[5]),
            )
            for row in conn.execute(f"PRAGMA table_info({quoted})")
        )
        unique_indexes = tuple(
            sorted(
                tuple(info[2] for info in conn.execute(f"PRAGMA index_info({_quote(index[1])})"))
                for index in conn.execute(f"PRAGMA index_list({quoted})")
                if index[2]
            )
        )
        foreign_keys = tuple(
            sorted(
                (row[3], row[2], row[4])
                for row in conn.execute(f"PRAGMA foreign_key_list({quoted})")
            )
        )
        result[table] = TableInfo(columns, unique_indexes, foreign_keys)
    return result


# --- Tabellen-DDL (aus dem historischen Code, Name = erste Variante mit dieser Fassung) ---

_PRODUCTS_S1 = """
CREATE TABLE products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    produktbezeichnung TEXT,
    gem_en TEXT,
    produktname TEXT,
    hersteller TEXT,
    herstellungsjahr TEXT,
    kaufdatum TEXT,
    datum_einsatz TEXT,
    einzelidentifikation TEXT,
    seriennummer TEXT,
    gal_datei TEXT,
    gal_link TEXT
)
"""

_SYSTEMS_S1 = """
CREATE TABLE systems (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT,
    gal_datei TEXT,
    gal_link TEXT
)
"""

_SYSTEM_PARTS_S1 = """
CREATE TABLE system_parts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id INTEGER NOT NULL,
    part_index INTEGER NOT NULL,
    produktbezeichnung TEXT,
    gem_en TEXT,
    produktname TEXT,
    hersteller TEXT,
    herstellungsjahr TEXT,
    kaufdatum TEXT,
    datum_einsatz TEXT,
    einzelidentifikation TEXT,
    seriennummer TEXT,
    FOREIGN KEY(system_id) REFERENCES systems(id)
)
"""

_ITEM_LOCKS_S2 = """
CREATE TABLE item_locks (
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

_VERLEIH_PLANUNG_S2 = """
CREATE TABLE verleih_planung (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_type TEXT NOT NULL,
    item_id INTEGER NOT NULL,
    item_label TEXT,
    von_datum TEXT NOT NULL,
    rueckgabe_datum TEXT NOT NULL,
    entleiher TEXT,
    entleiher_email TEXT,
    entleiher_telefon TEXT,
    entleiher_adresse TEXT,
    signature_data TEXT,
    quick_check_out INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'lent',
    checkout_at TEXT,
    return_comment TEXT,
    return_signature_data TEXT,
    quick_check_return INTEGER NOT NULL DEFAULT 0,
    returned_at TEXT,
    created_at TEXT NOT NULL
)
"""

_PRODUCTS_S3 = """
CREATE TABLE products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    produktbezeichnung TEXT,
    gem_en TEXT,
    produktname TEXT,
    hersteller TEXT,
    herstellungsjahr TEXT,
    kaufdatum TEXT,
    datum_einsatz TEXT,
    einzelidentifikation TEXT,
    seriennummer TEXT,
    gal_datei TEXT,
    gal_link TEXT,
    naechste_pruefung_am TEXT,
    last_psa_pruefung_am TEXT,
    last_psa_pruefung_kommentar TEXT
)
"""

_SYSTEMS_S3 = """
CREATE TABLE systems (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT,
    gal_datei TEXT,
    gal_link TEXT,
    naechste_pruefung_am TEXT,
    last_psa_pruefung_am TEXT,
    last_psa_pruefung_kommentar TEXT
)
"""

_PSA_PRUEFUNGSBERICHTE_S3 = """
CREATE TABLE psa_pruefungsberichte (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    sachkundiger_name TEXT NOT NULL,
    sachkundig_bestaetigt INTEGER NOT NULL DEFAULT 0,
    signature_data TEXT NOT NULL
)
"""

_PSA_PRUEFUNGSBERICHT_ITEMS_S3 = """
CREATE TABLE psa_pruefungsbericht_items (
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

_VERLEIH_PLANUNG_S3 = """
CREATE TABLE verleih_planung (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_type TEXT NOT NULL,
    item_id INTEGER NOT NULL,
    item_label TEXT,
    von_datum TEXT NOT NULL,
    rueckgabe_datum TEXT NOT NULL,
    entleiher TEXT,
    entleiher_email TEXT,
    entleiher_telefon TEXT,
    entleiher_adresse TEXT,
    signature_data TEXT,
    quick_check_out INTEGER NOT NULL DEFAULT 0,
    gal_provided_out INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'lent',
    checkout_at TEXT,
    return_comment TEXT,
    return_signature_data TEXT,
    quick_check_return INTEGER NOT NULL DEFAULT 0,
    returned_at TEXT,
    created_at TEXT NOT NULL
)
"""

_VERLEIH_PLANUNG_S4 = """
CREATE TABLE verleih_planung (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_type TEXT NOT NULL,
    item_id INTEGER NOT NULL,
    item_label TEXT,
    von_datum TEXT NOT NULL,
    rueckgabe_datum TEXT NOT NULL,
    entleiher TEXT,
    entleiher_email TEXT,
    entleiher_telefon TEXT,
    entleiher_adresse TEXT,
    signature_data TEXT,
    quick_check_out INTEGER NOT NULL DEFAULT 0,
    gal_provided_out INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'lent',
    checkout_at TEXT,
    return_comment TEXT,
    return_signature_data TEXT,
    quick_check_return INTEGER NOT NULL DEFAULT 0,
    returned_at TEXT,
    created_at TEXT NOT NULL,
    ausgebende_person TEXT,
    ruecknehmende_person TEXT
)
"""

_VERLEIH_PLANUNG_S5 = """
CREATE TABLE verleih_planung (
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

_ARCHIVED_ITEMS_S5 = """
CREATE TABLE archived_items (
    archived_item_id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_type TEXT NOT NULL,
    source_item_id INTEGER NOT NULL,
    item_label TEXT NOT NULL,
    archived_at TEXT NOT NULL,
    archived_by TEXT,
    kommentar TEXT
)
"""

_PRODUCTS_S6 = """
CREATE TABLE products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    produktbezeichnung TEXT,
    gem_en TEXT,
    produktname TEXT,
    hersteller TEXT,
    herstellungsjahr TEXT,
    kaufdatum TEXT,
    datum_einsatz TEXT,
    einzelidentifikation TEXT,
    seriennummer TEXT,
    gal_datei TEXT,
    gal_link TEXT,
    naechste_pruefung_am TEXT,
    last_psa_pruefung_am TEXT,
    last_psa_pruefung_kommentar TEXT,
    last_psa_durchgefallen_am TEXT
)
"""

_SYSTEMS_S6 = """
CREATE TABLE systems (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT,
    gal_datei TEXT,
    gal_link TEXT,
    naechste_pruefung_am TEXT,
    last_psa_pruefung_am TEXT,
    last_psa_pruefung_kommentar TEXT,
    last_psa_durchgefallen_am TEXT
)
"""

_PRODUCTS_S7 = """
CREATE TABLE products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    produktbezeichnung TEXT,
    gem_en TEXT,
    produktname TEXT,
    hersteller TEXT,
    herstellungsjahr TEXT,
    kaufdatum TEXT,
    datum_einsatz TEXT,
    einzelidentifikation TEXT,
    seriennummer TEXT,
    gal_datei TEXT,
    gal_link TEXT,
    keine_psa INTEGER NOT NULL DEFAULT 0,
    naechste_pruefung_am TEXT,
    last_psa_pruefung_am TEXT,
    last_psa_pruefung_kommentar TEXT,
    last_psa_durchgefallen_am TEXT
)
"""

_SYSTEMS_S7 = """
CREATE TABLE systems (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT,
    gal_datei TEXT,
    gal_link TEXT,
    keine_psa INTEGER NOT NULL DEFAULT 0,
    naechste_pruefung_am TEXT,
    last_psa_pruefung_am TEXT,
    last_psa_pruefung_kommentar TEXT,
    last_psa_durchgefallen_am TEXT
)
"""

_VERLEIH_PLANUNG_S2_AKTUALISIERT = """
CREATE TABLE verleih_planung (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_type TEXT NOT NULL,
    item_id INTEGER NOT NULL,
    item_label TEXT,
    von_datum TEXT NOT NULL,
    rueckgabe_datum TEXT NOT NULL,
    entleiher TEXT,
    entleiher_email TEXT,
    entleiher_telefon TEXT,
    entleiher_adresse TEXT,
    signature_data TEXT,
    quick_check_out INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'lent',
    checkout_at TEXT,
    return_comment TEXT,
    return_signature_data TEXT,
    quick_check_return INTEGER NOT NULL DEFAULT 0,
    returned_at TEXT,
    created_at TEXT NOT NULL,
    ausgebende_person TEXT,
    gal_provided_out INTEGER NOT NULL DEFAULT 0,
    ruecknehmende_person TEXT
)
"""

_PRODUCTS_S3_AKTUALISIERT = """
CREATE TABLE products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    produktbezeichnung TEXT,
    gem_en TEXT,
    produktname TEXT,
    hersteller TEXT,
    herstellungsjahr TEXT,
    kaufdatum TEXT,
    datum_einsatz TEXT,
    einzelidentifikation TEXT,
    seriennummer TEXT,
    gal_datei TEXT,
    gal_link TEXT,
    naechste_pruefung_am TEXT,
    last_psa_pruefung_am TEXT,
    last_psa_pruefung_kommentar TEXT,
    keine_psa INTEGER NOT NULL DEFAULT 0,
    last_psa_durchgefallen_am TEXT
)
"""

_SYSTEMS_S3_AKTUALISIERT = """
CREATE TABLE systems (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT,
    gal_datei TEXT,
    gal_link TEXT,
    naechste_pruefung_am TEXT,
    last_psa_pruefung_am TEXT,
    last_psa_pruefung_kommentar TEXT,
    keine_psa INTEGER NOT NULL DEFAULT 0,
    last_psa_durchgefallen_am TEXT
)
"""

_PRODUCTS_S6_AKTUALISIERT = """
CREATE TABLE products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    produktbezeichnung TEXT,
    gem_en TEXT,
    produktname TEXT,
    hersteller TEXT,
    herstellungsjahr TEXT,
    kaufdatum TEXT,
    datum_einsatz TEXT,
    einzelidentifikation TEXT,
    seriennummer TEXT,
    gal_datei TEXT,
    gal_link TEXT,
    naechste_pruefung_am TEXT,
    last_psa_pruefung_am TEXT,
    last_psa_pruefung_kommentar TEXT,
    last_psa_durchgefallen_am TEXT,
    keine_psa INTEGER NOT NULL DEFAULT 0
)
"""

_SYSTEMS_S6_AKTUALISIERT = """
CREATE TABLE systems (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT,
    gal_datei TEXT,
    gal_link TEXT,
    naechste_pruefung_am TEXT,
    last_psa_pruefung_am TEXT,
    last_psa_pruefung_kommentar TEXT,
    last_psa_durchgefallen_am TEXT,
    keine_psa INTEGER NOT NULL DEFAULT 0
)
"""

_PRODUCTS_S1_NUR_VERLEIH = """
CREATE TABLE products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    produktbezeichnung TEXT,
    gem_en TEXT,
    produktname TEXT,
    hersteller TEXT,
    herstellungsjahr TEXT,
    kaufdatum TEXT,
    datum_einsatz TEXT,
    einzelidentifikation TEXT,
    seriennummer TEXT,
    gal_datei TEXT,
    gal_link TEXT,
    naechste_pruefung_am TEXT,
    keine_psa INTEGER NOT NULL DEFAULT 0
)
"""

_SYSTEMS_S1_NUR_VERLEIH = """
CREATE TABLE systems (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT,
    gal_datei TEXT,
    gal_link TEXT,
    naechste_pruefung_am TEXT,
    keine_psa INTEGER NOT NULL DEFAULT 0
)
"""

_PRODUCTS_S3_NUR_VERLEIH = """
CREATE TABLE products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    produktbezeichnung TEXT,
    gem_en TEXT,
    produktname TEXT,
    hersteller TEXT,
    herstellungsjahr TEXT,
    kaufdatum TEXT,
    datum_einsatz TEXT,
    einzelidentifikation TEXT,
    seriennummer TEXT,
    gal_datei TEXT,
    gal_link TEXT,
    naechste_pruefung_am TEXT,
    last_psa_pruefung_am TEXT,
    last_psa_pruefung_kommentar TEXT,
    keine_psa INTEGER NOT NULL DEFAULT 0
)
"""

_SYSTEMS_S3_NUR_VERLEIH = """
CREATE TABLE systems (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT,
    gal_datei TEXT,
    gal_link TEXT,
    naechste_pruefung_am TEXT,
    last_psa_pruefung_am TEXT,
    last_psa_pruefung_kommentar TEXT,
    keine_psa INTEGER NOT NULL DEFAULT 0
)
"""

# --- Varianten ---

VARIANTS: dict[str, SchemaVariant] = {
    variant.name: variant
    for variant in (
        SchemaVariant(
            name="s1",
            description="Stand S1 (nur Stammdaten), frisch angelegt",
            base_commit="93ec23f",
            upgrade=None,
            main=(
                _PRODUCTS_S1,
                _SYSTEMS_S1,
                _SYSTEM_PARTS_S1,
            ),
        ),
        SchemaVariant(
            name="s2",
            description="Stand S2 (Sperren und Verleih), frisch angelegt",
            base_commit="0480fd0",
            upgrade=None,
            main=(
                _PRODUCTS_S1,
                _SYSTEMS_S1,
                _SYSTEM_PARTS_S1,
                _ITEM_LOCKS_S2,
                _VERLEIH_PLANUNG_S2,
            ),
        ),
        SchemaVariant(
            name="s3",
            description="Stand S3 (PSA-Prüfung, Prüfberichte), frisch angelegt",
            base_commit="59d5b3c",
            upgrade=None,
            main=(
                _PRODUCTS_S3,
                _SYSTEMS_S3,
                _SYSTEM_PARTS_S1,
                _ITEM_LOCKS_S2,
                _PSA_PRUEFUNGSBERICHTE_S3,
                _PSA_PRUEFUNGSBERICHT_ITEMS_S3,
                _VERLEIH_PLANUNG_S3,
            ),
        ),
        SchemaVariant(
            name="s4",
            description="Stand S4 (ausgebende/rücknehmende Person), frisch angelegt",
            base_commit="105fc0f",
            upgrade=None,
            main=(
                _PRODUCTS_S3,
                _SYSTEMS_S3,
                _SYSTEM_PARTS_S1,
                _ITEM_LOCKS_S2,
                _VERLEIH_PLANUNG_S4,
                _PSA_PRUEFUNGSBERICHTE_S3,
                _PSA_PRUEFUNGSBERICHT_ITEMS_S3,
            ),
        ),
        SchemaVariant(
            name="s5",
            description="Stand S5 (Archivdatenbank), frisch angelegt",
            base_commit="59ec978",
            upgrade=None,
            main=(
                _PRODUCTS_S3,
                _SYSTEMS_S3,
                _SYSTEM_PARTS_S1,
                _ITEM_LOCKS_S2,
                _VERLEIH_PLANUNG_S5,
                _PSA_PRUEFUNGSBERICHTE_S3,
                _PSA_PRUEFUNGSBERICHT_ITEMS_S3,
            ),
            archive=(
                _PRODUCTS_S3,
                _SYSTEMS_S3,
                _SYSTEM_PARTS_S1,
                _ITEM_LOCKS_S2,
                _VERLEIH_PLANUNG_S5,
                _PSA_PRUEFUNGSBERICHTE_S3,
                _PSA_PRUEFUNGSBERICHT_ITEMS_S3,
                _ARCHIVED_ITEMS_S5,
            ),
        ),
        SchemaVariant(
            name="s6",
            description="Stand S6 (Prüfung nicht bestanden), frisch angelegt",
            base_commit="176ac49",
            upgrade=None,
            main=(
                _PRODUCTS_S6,
                _SYSTEMS_S6,
                _SYSTEM_PARTS_S1,
                _ITEM_LOCKS_S2,
                _VERLEIH_PLANUNG_S5,
                _PSA_PRUEFUNGSBERICHTE_S3,
                _PSA_PRUEFUNGSBERICHT_ITEMS_S3,
            ),
            archive=(
                _PRODUCTS_S6,
                _SYSTEMS_S6,
                _SYSTEM_PARTS_S1,
                _ITEM_LOCKS_S2,
                _VERLEIH_PLANUNG_S5,
                _PSA_PRUEFUNGSBERICHTE_S3,
                _PSA_PRUEFUNGSBERICHT_ITEMS_S3,
                _ARCHIVED_ITEMS_S5,
            ),
        ),
        SchemaVariant(
            name="s7",
            description="Stand S7 (keine PSA), frisch angelegt; aktueller Stand",
            base_commit="b441ab6",
            upgrade=None,
            main=(
                _PRODUCTS_S7,
                _SYSTEMS_S7,
                _SYSTEM_PARTS_S1,
                _ITEM_LOCKS_S2,
                _VERLEIH_PLANUNG_S5,
                _PSA_PRUEFUNGSBERICHTE_S3,
                _PSA_PRUEFUNGSBERICHT_ITEMS_S3,
            ),
            archive=(
                _PRODUCTS_S7,
                _SYSTEMS_S7,
                _SYSTEM_PARTS_S1,
                _ITEM_LOCKS_S2,
                _VERLEIH_PLANUNG_S5,
                _PSA_PRUEFUNGSBERICHTE_S3,
                _PSA_PRUEFUNGSBERICHT_ITEMS_S3,
                _ARCHIVED_ITEMS_S5,
            ),
        ),
        SchemaVariant(
            name="s2_aktualisiert",
            description="Stand S2, danach mit dem aktuellen PSA-Programm geöffnet",
            base_commit="0480fd0",
            upgrade=Upgrade.PSA,
            main=(
                _PRODUCTS_S7,
                _SYSTEMS_S7,
                _SYSTEM_PARTS_S1,
                _ITEM_LOCKS_S2,
                _VERLEIH_PLANUNG_S2_AKTUALISIERT,
                _PSA_PRUEFUNGSBERICHTE_S3,
                _PSA_PRUEFUNGSBERICHT_ITEMS_S3,
            ),
        ),
        SchemaVariant(
            name="s3_aktualisiert",
            description="Stand S3 oder S4, danach mit dem aktuellen PSA-Programm geöffnet",
            base_commit="59d5b3c",
            upgrade=Upgrade.PSA,
            main=(
                _PRODUCTS_S3_AKTUALISIERT,
                _SYSTEMS_S3_AKTUALISIERT,
                _SYSTEM_PARTS_S1,
                _ITEM_LOCKS_S2,
                _PSA_PRUEFUNGSBERICHTE_S3,
                _PSA_PRUEFUNGSBERICHT_ITEMS_S3,
                _VERLEIH_PLANUNG_S4,
            ),
        ),
        SchemaVariant(
            name="s5_aktualisiert",
            description="Stand S5, danach mit dem aktuellen PSA-Programm geöffnet",
            base_commit="59ec978",
            upgrade=Upgrade.PSA,
            main=(
                _PRODUCTS_S3_AKTUALISIERT,
                _SYSTEMS_S3_AKTUALISIERT,
                _SYSTEM_PARTS_S1,
                _ITEM_LOCKS_S2,
                _VERLEIH_PLANUNG_S5,
                _PSA_PRUEFUNGSBERICHTE_S3,
                _PSA_PRUEFUNGSBERICHT_ITEMS_S3,
            ),
            archive=(
                _PRODUCTS_S3,
                _SYSTEMS_S3,
                _SYSTEM_PARTS_S1,
                _ITEM_LOCKS_S2,
                _VERLEIH_PLANUNG_S5,
                _PSA_PRUEFUNGSBERICHTE_S3,
                _PSA_PRUEFUNGSBERICHT_ITEMS_S3,
                _ARCHIVED_ITEMS_S5,
            ),
        ),
        SchemaVariant(
            name="s6_aktualisiert",
            description="Stand S6, danach mit dem aktuellen PSA- oder Verleih-Programm geöffnet",
            base_commit="176ac49",
            upgrade=Upgrade.PSA,
            main=(
                _PRODUCTS_S6_AKTUALISIERT,
                _SYSTEMS_S6_AKTUALISIERT,
                _SYSTEM_PARTS_S1,
                _ITEM_LOCKS_S2,
                _VERLEIH_PLANUNG_S5,
                _PSA_PRUEFUNGSBERICHTE_S3,
                _PSA_PRUEFUNGSBERICHT_ITEMS_S3,
            ),
            archive=(
                _PRODUCTS_S6,
                _SYSTEMS_S6,
                _SYSTEM_PARTS_S1,
                _ITEM_LOCKS_S2,
                _VERLEIH_PLANUNG_S5,
                _PSA_PRUEFUNGSBERICHTE_S3,
                _PSA_PRUEFUNGSBERICHT_ITEMS_S3,
                _ARCHIVED_ITEMS_S5,
            ),
        ),
        SchemaVariant(
            name="s1_nur_verleih",
            description="Stand S1, danach nur mit dem aktuellen Verleih-Programm geöffnet",
            base_commit="93ec23f",
            upgrade=Upgrade.VERLEIH,
            main=(
                _PRODUCTS_S1_NUR_VERLEIH,
                _SYSTEMS_S1_NUR_VERLEIH,
                _SYSTEM_PARTS_S1,
                _ITEM_LOCKS_S2,
                _VERLEIH_PLANUNG_S5,
            ),
        ),
        SchemaVariant(
            name="s2_nur_verleih",
            description="Stand S2, danach nur mit dem aktuellen Verleih-Programm geöffnet",
            base_commit="0480fd0",
            upgrade=Upgrade.VERLEIH,
            main=(
                _PRODUCTS_S1_NUR_VERLEIH,
                _SYSTEMS_S1_NUR_VERLEIH,
                _SYSTEM_PARTS_S1,
                _ITEM_LOCKS_S2,
                _VERLEIH_PLANUNG_S2_AKTUALISIERT,
            ),
        ),
        SchemaVariant(
            name="s3_nur_verleih",
            description="Stand S3 oder S4, danach nur mit dem aktuellen Verleih-Programm geöffnet",
            base_commit="59d5b3c",
            upgrade=Upgrade.VERLEIH,
            main=(
                _PRODUCTS_S3_NUR_VERLEIH,
                _SYSTEMS_S3_NUR_VERLEIH,
                _SYSTEM_PARTS_S1,
                _ITEM_LOCKS_S2,
                _PSA_PRUEFUNGSBERICHTE_S3,
                _PSA_PRUEFUNGSBERICHT_ITEMS_S3,
                _VERLEIH_PLANUNG_S4,
            ),
        ),
        SchemaVariant(
            name="s5_nur_verleih",
            description="Stand S5, danach nur mit dem aktuellen Verleih-Programm geöffnet",
            base_commit="59ec978",
            upgrade=Upgrade.VERLEIH,
            main=(
                _PRODUCTS_S3_NUR_VERLEIH,
                _SYSTEMS_S3_NUR_VERLEIH,
                _SYSTEM_PARTS_S1,
                _ITEM_LOCKS_S2,
                _VERLEIH_PLANUNG_S5,
                _PSA_PRUEFUNGSBERICHTE_S3,
                _PSA_PRUEFUNGSBERICHT_ITEMS_S3,
            ),
            archive=(
                _PRODUCTS_S3,
                _SYSTEMS_S3,
                _SYSTEM_PARTS_S1,
                _ITEM_LOCKS_S2,
                _VERLEIH_PLANUNG_S5,
                _PSA_PRUEFUNGSBERICHTE_S3,
                _PSA_PRUEFUNGSBERICHT_ITEMS_S3,
                _ARCHIVED_ITEMS_S5,
            ),
        ),
    )
}
