"""Import der Altdaten nach PSAgent 2.0 (#10): `psa.db`, `psa_archiv.db` und `GAL/`.

Ablauf (Konzept, Abschnitt 4):

- Die Quellen werden nur lesend geöffnet (SQLite `mode=ro`), Spalten über ihren Namen gelesen.
  So funktionieren alle Schema-Varianten (`docs/altsystem-testdaten.md`).
- Alles läuft in einer Transaktion. Der Probelauf (`dry_run=True`) rollt am Ende zurück und
  speichert keine GAL-Dateien; der Bericht ist derselbe.
- Ist die Zieldatenbank nicht leer (Teile, Entleiher, Prüfungen, Altberichte), bricht der
  Import ab. Benutzerkonten dürfen schon da sein.

Was wohin geht:

- Produkte und Systeme werden Teile mit Bauteilen. Bauteile ohne jede Angabe fallen weg (das
  Altsystem hat fest drei Plätze je System). Daten in `TT.MM.JJJJ` werden umgewandelt,
  ungültige Angaben leer übernommen und gemeldet.
- EI-Probleme (fehlt, kollidiert nach Normalisierung): Aktive Teile werden abgelehnt und vor dem
  echten Import im Altsystem bereinigt. Ausgesonderte Teile bekommen eine Ersatz-EI
  `ALT-P<id>-<pos>` bzw. `ALT-S<id>-<pos>`, damit ihre Historie erhalten bleibt.
- Prüfstatus: Wer im Altsystem ein gültiges Datum „nächste Prüfung“ hat, wird über eine
  Übernahme-Runde „Altbestand“ freigegeben, auch wenn das Datum verstrichen ist (dann fällig).
  „keine PSA“ bleibt erhalten. Ohne Datum bleibt ein Teil „neu, nicht freigegeben“.
- Sperren und Entsperrungen werden Vorgänge mit Zeitpunkt und Kommentar. Archivierte Teile
  kommen aus `psa_archiv.db` und werden „ausgesondert“.
- Ausleihen: je Zeile ein Ausleihvorgang. Entleiher mit gleichem Namen und gleicher E-Mail oder
  Telefonnummer werden zusammengeführt. Unterschriften sind „Altbestand, ungesichert“.
  Überschneidende aktive Ausleihen werden abgelehnt.
- Prüfberichte werden wörtlich als Altberichte übernommen, ohne Wirkung (`legacy.models`).

Der Bericht nennt Datensätze nur über ihre Nummer im Altsystem und die EI, nie über Namen,
Kontaktdaten oder Unterschriften. Echte Altdaten liest nur Felix (CLAUDE.md, Regel 1).
"""

import hashlib
import json
import re
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path, PurePosixPath
from typing import Any
from zoneinfo import ZoneInfo

from django.db import IntegrityError, transaction
from django.db.backends.postgresql.psycopg_any import DateRange
from django.utils import timezone

from inspections.models import InspectionRound, Outcome
from inspections.services import adopt_legacy_round, close_round, record_result, start_round
from inventory.models import Document, EventKind, Part, PartKind, normalize_ei
from inventory.services import (
    PDF_MAGIC,
    InventoryError,
    create_product,
    create_system,
    link_document,
    record_event,
    store_document_file,
)
from legacy.models import LegacyReport, LegacyReportItem
from lending.models import (
    Booking,
    Borrower,
    BorrowerContact,
    Loan,
    LoanEvent,
    LoanEventKind,
    LoanStatus,
    Signature,
    SignatureOrigin,
)

MAIN_DB = "psa.db"
ARCHIVE_DB = "psa_archiv.db"
GAL_DIR = "GAL"
LEGACY_TZ = ZoneInfo("Europe/Berlin")
IMPORT_NOTE = "Übernahme aus dem Altsystem"
ROUND_INSPECTOR = "Altsystem (Übernahme)"

TABLES = (
    "products",
    "systems",
    "system_parts",
    "item_locks",
    "verleih_planung",
    "psa_pruefungsberichte",
    "psa_pruefungsbericht_items",
    "archived_items",
)
KIND_LABEL = {PartKind.PRODUCT: "Produkt", PartKind.SYSTEM: "System"}

# Altsystem-Spalte → Bauteilfeld, Höchstlänge
TEXT_FIELDS = (
    ("produktbezeichnung", "designation", "Produktbezeichnung", 200),
    ("gem_en", "standard", "gem. EN", 100),
    ("produktname", "model_name", "Produktname", 200),
    ("hersteller", "manufacturer", "Hersteller", 200),
    ("seriennummer", "serial_number", "Seriennummer", 100),
)
COMPONENT_COLUMNS = (
    *(column for column, *_ in TEXT_FIELDS),
    "herstellungsjahr",
    "kaufdatum",
    "datum_einsatz",
    "einzelidentifikation",
)
DATE_FORMATS = ("%Y-%m-%d", "%d.%m.%Y")
TIMESTAMP_FORMATS = ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d")
LOAN_STATUSES = {"reserved": LoanStatus.RESERVED, "lent": LoanStatus.LENT}

Row = dict[str, Any]
ItemKey = tuple[str, int]


class LegacyImportError(Exception):
    """Der Import kann nicht beginnen (Quelle fehlt, Ziel nicht leer)."""


class _RejectedError(Exception):
    """Ein Datensatz wird nicht übernommen; die Meldung kommt in den Bericht."""


# --- Bericht ---


@dataclass
class Counts:
    read: int = 0
    imported: int = 0
    rejected: int = 0
    skipped: int = 0


@dataclass
class ImportReport:
    """Ergebnis eines Laufs. Enthält keine Personendaten."""

    dry_run: bool
    started_at: datetime = field(default_factory=timezone.now)
    sources: list[tuple[str, str]] = field(default_factory=list)
    counts: dict[str, Counts] = field(default_factory=lambda: {t: Counts() for t in TABLES})
    rejected: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    clarifications: list[str] = field(default_factory=list)

    def render(self) -> str:
        title = "Probelauf" if self.dry_run else "Import"
        lines = [
            f"# Importbericht ({title})",
            "",
            f"Zeitpunkt: {timezone.localtime(self.started_at):%d.%m.%Y %H:%M}",
            "",
            "Der Probelauf schreibt nichts." if self.dry_run else "Alle Daten sind übernommen.",
            "",
            "## Quellen (SHA-256)",
            "",
            "| Quelle | SHA-256 |",
            "| --- | --- |",
            *(f"| {name} | `{digest}` |" for name, digest in self.sources),
            "",
            "## Anzahl je Tabelle",
            "",
            "| Tabelle | gelesen | übernommen | abgelehnt | übersprungen |",
            "| --- | ---: | ---: | ---: | ---: |",
            *(
                f"| {table} | {c.read} | {c.imported} | {c.rejected} | {c.skipped} |"
                for table, c in self.counts.items()
            ),
        ]
        for heading, entries in (
            ("Abgelehnt", self.rejected),
            ("Warnungen", self.warnings),
            ("Klärungspunkte", self.clarifications),
        ):
            lines += ["", f"## {heading} ({len(entries)})", ""]
            lines += [f"- {entry}" for entry in entries] or ["keine"]
        return "\n".join(lines) + "\n"


# --- Lesen der Quellen ---


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_dir(path: Path) -> str:
    """Prüfsumme eines Ordners: Name und SHA-256 jeder Datei, sortiert."""
    lines = [
        f"{file.relative_to(path).as_posix()}\t{_sha256_file(file)}"
        for file in sorted(p for p in path.rglob("*") if p.is_file())
    ]
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


@contextmanager
def _open_readonly(path: Path) -> Iterator[sqlite3.Connection]:
    with closing(sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        yield conn


def _read_tables(path: Path) -> dict[str, list[Row]]:
    with _open_readonly(path) as conn:
        present = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        # Tabellennamen kommen aus der festen Liste TABLES, nie aus Eingaben.
        return {
            table: [dict(row) for row in conn.execute(f'SELECT * FROM "{table}" ORDER BY rowid')]  # noqa: S608
            for table in TABLES
            if table in present
        }


# --- Umwandeln einzelner Werte ---


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _parse_date(raw: str) -> date | None:
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def _parse_timestamp(raw: str) -> datetime | None:
    for fmt in TIMESTAMP_FORMATS:
        try:
            return timezone.make_aware(datetime.strptime(raw, fmt), LEGACY_TZ)
        except ValueError:
            continue
    return None


def _strokes(raw: str) -> list[Any] | None:
    """Strichliste des Altsystems, wenn sie gültig ist: [[[x, y], …], …]."""
    try:
        strokes = json.loads(raw)
    except (TypeError, ValueError):
        return None
    valid = (
        isinstance(strokes, list)
        and bool(strokes)
        and all(
            isinstance(stroke, list)
            and all(
                isinstance(point, list)
                and len(point) == 2
                and all(isinstance(v, int | float) for v in point)
                for point in stroke
            )
            for stroke in strokes
        )
    )
    return strokes if valid else None


def _name_key(name: str) -> str:
    return " ".join(name.split()).casefold()


@dataclass
class _Item:
    """Ein Teil des Altsystems: aktiv (psa.db) oder archiviert (psa_archiv.db)."""

    kind: PartKind
    legacy_id: int
    row: Row
    parts: list[Row]
    archived: Row | None = None
    components: list[dict[str, Any]] = field(default_factory=list)
    replaced_eis: list[tuple[int, str, str]] = field(default_factory=list)
    part: Part | None = None

    @property
    def key(self) -> ItemKey:
        return (self.kind.value, self.legacy_id)

    @property
    def ref(self) -> str:
        return f"{KIND_LABEL[self.kind]} #{self.legacy_id}"

    @property
    def created(self) -> Part:
        """Das angelegte Teil; erst nach `_create_parts` vorhanden."""
        if self.part is None:
            raise RuntimeError(f"{self.ref} ist noch nicht angelegt.")
        return self.part

    @property
    def ei_ref(self) -> str:
        eis = [c["ei"] for c in self.components if c.get("ei")]
        return f"{self.ref} (EI {eis[0]})" if eis else self.ref


class LegacyImporter:
    """Ein Importlauf. `run()` liefert den Bericht; im Probelauf bleibt die Datenbank leer."""

    def __init__(self, source: Path, *, dry_run: bool) -> None:
        self.source = source
        self.dry_run = dry_run
        self.report = ImportReport(dry_run=dry_run)
        self.items: dict[ItemKey, _Item] = {}
        self.borrowers: list[tuple[str, set[str], set[str], Borrower]] = []
        # Erst nach erfolgreicher Übernahme einer Ausleihe merken (Savepoint kann zurückrollen).
        self.pending_borrower: tuple[str, set[str], set[str], Borrower] | None = None
        self.used_gal_files: set[Path] = set()
        self.gal_dir = source / GAL_DIR

    # --- Rahmen ---

    def run(self) -> ImportReport:
        main_path = self.source / MAIN_DB
        if not main_path.is_file():
            raise LegacyImportError(f"{main_path} fehlt.")
        self._check_target_empty()
        self.report.sources.append((MAIN_DB, _sha256_file(main_path)))
        main = _read_tables(main_path)
        archive_path = self.source / ARCHIVE_DB
        archive: dict[str, list[Row]] = {}
        if archive_path.is_file():
            self.report.sources.append((ARCHIVE_DB, _sha256_file(archive_path)))
            archive = _read_tables(archive_path)
        if self.gal_dir.is_dir():
            self.report.sources.append((f"{GAL_DIR}/", _sha256_dir(self.gal_dir)))

        with transaction.atomic():
            self._import(main, archive)
            if self.dry_run:
                transaction.set_rollback(True)
        return self.report

    def _check_target_empty(self) -> None:
        for model in (Part, Document, Borrower, Booking, InspectionRound, LegacyReport):
            if model.objects.exists():
                raise LegacyImportError(
                    f"Die Zieldatenbank enthält bereits Daten ({model._meta.verbose_name_plural}). "
                    "Der Import läuft nur in eine leere Datenbank."
                )

    def _count(self, table: str) -> Counts:
        return self.report.counts[table]

    def _import(self, main: dict[str, list[Row]], archive: dict[str, list[Row]]) -> None:
        self._mark_gal_references(main, archive)
        self._collect_items(main, archive)
        self._resolve_eis()
        self._create_parts()
        self._adopt_next_checks()
        self._import_locks(main, archive)
        self._retire_archived()
        self._import_loans(main, archive)
        self._import_reports(main, archive)
        self._report_orphan_gal_files()

    # --- Teile sammeln ---

    def _collect_items(self, main: dict[str, list[Row]], archive: dict[str, list[Row]]) -> None:
        parts_by_system: dict[int, list[Row]] = {}
        for row in main.get("system_parts", []):
            parts_by_system.setdefault(_int(row.get("system_id")), []).append(row)
        self._count("system_parts").read += len(main.get("system_parts", []))

        for row in main.get("products", []):
            self._count("products").read += 1
            item = _Item(PartKind.PRODUCT, _int(row["id"]), row, [row])
            self.items[item.key] = item
        for row in main.get("systems", []):
            self._count("systems").read += 1
            parts = parts_by_system.get(_int(row["id"]), [])
            self.items[(PartKind.SYSTEM.value, _int(row["id"]))] = _Item(
                PartKind.SYSTEM, _int(row["id"]), row, parts
            )
        orphans = set(parts_by_system) - {_int(r["id"]) for r in main.get("systems", [])}
        for system_id in sorted(orphans):
            count = len(parts_by_system[system_id])
            self._count("system_parts").rejected += count
            self.report.rejected.append(f"{count} Bauteil(e) zu System #{system_id}: System fehlt")

        archived_rows = {
            (PartKind.PRODUCT.value, _int(r["id"])): r for r in archive.get("products", [])
        } | {(PartKind.SYSTEM.value, _int(r["id"])): r for r in archive.get("systems", [])}
        archived_parts: dict[int, list[Row]] = {}
        for row in archive.get("system_parts", []):
            archived_parts.setdefault(_int(row.get("system_id")), []).append(row)

        for entry in archive.get("archived_items", []):
            counts = self._count("archived_items")
            counts.read += 1
            kind = PartKind.SYSTEM if entry.get("item_type") == "system" else PartKind.PRODUCT
            legacy_id = _int(entry.get("source_item_id"))
            ref = f"{KIND_LABEL[kind]} #{legacy_id}"
            key = (kind.value, legacy_id)
            if key in self.items and self.items[key].archived is not None:
                counts.skipped += 1
                self.report.warnings.append(
                    f"Archiveintrag #{_int(entry.get('archived_item_id'))}: {ref} doppelt "
                    "archiviert; der erste Eintrag gilt"
                )
                continue
            if key in self.items:
                counts.skipped += 1
                self.report.clarifications.append(
                    f"Archiveintrag #{_int(entry.get('archived_item_id'))}: {ref} steht noch im "
                    "Bestand; übernommen als aktives Teil"
                )
                continue
            archived_row = archived_rows.get(key)
            if archived_row is None:
                counts.rejected += 1
                self.report.rejected.append(
                    f"Archiveintrag #{_int(entry.get('archived_item_id'))}: keine Daten zu {ref} "
                    "im Archiv"
                )
                continue
            parts = (
                [archived_row] if kind == PartKind.PRODUCT else archived_parts.get(legacy_id, [])
            )
            if kind == PartKind.SYSTEM:
                self._count("system_parts").read += len(parts)
            self.items[key] = _Item(kind, legacy_id, archived_row, parts, archived=entry)

    def _component(self, item: _Item, index: int, raw: Row) -> dict[str, Any]:
        where = item.ref if item.kind == PartKind.PRODUCT else f"{item.ref}, Bauteil {index}"
        fields: dict[str, Any] = {}
        for column, name, label, limit in TEXT_FIELDS:
            value = _text(raw.get(column))
            if len(value) > limit:
                raise _RejectedError(f"{where}: {label} länger als {limit} Zeichen")
            fields[name] = value
        year = _text(raw.get("herstellungsjahr"))
        fields["year_of_manufacture"] = None
        if year:
            if re.fullmatch(r"\d{4}", year) and 1900 <= int(year) <= 2200:
                fields["year_of_manufacture"] = int(year)
            else:
                self.report.warnings.append(
                    f"{where}: Herstellungsjahr {year!r} ungültig, leer übernommen"
                )
        for column, name, label in (
            ("kaufdatum", "purchase_date", "Kaufdatum"),
            ("datum_einsatz", "first_use_date", "Datum 1. Einsatz"),
        ):
            fields[name] = self._date(raw.get(column), f"{where}: {label}")
        ei = _text(raw.get("einzelidentifikation"))
        if len(ei) > 100:
            raise _RejectedError(f"{where}: EI länger als 100 Zeichen")
        fields["ei"] = ei
        return fields

    def _date(self, value: Any, what: str) -> date | None:
        raw = _text(value)
        if not raw:
            return None
        parsed = _parse_date(raw)
        if parsed is None:
            self.report.warnings.append(f"{what} {raw!r} ungültig, leer übernommen")
        return parsed

    def _reject_item(self, item: _Item, reason: str) -> None:
        del self.items[item.key]
        table = "products" if item.kind == PartKind.PRODUCT else "systems"
        if item.archived is not None:
            self._count("archived_items").rejected += 1
        else:
            self._count(table).rejected += 1
        if item.kind == PartKind.SYSTEM:
            self._count("system_parts").rejected += len(item.parts)
        self.report.rejected.append(reason)

    # --- EI prüfen ---

    def _resolve_eis(self) -> None:
        for item in list(self.items.values()):
            try:
                rows = sorted(item.parts, key=lambda r: _int(r.get("part_index")) or 1)
                used = [r for r in rows if any(_text(r.get(c)) for c in COMPONENT_COLUMNS)]
                if item.kind == PartKind.SYSTEM:
                    self._count("system_parts").skipped += len(rows) - len(used)
                if not used:
                    raise _RejectedError(f"{item.ref}: keine Bauteile mit Angaben")
                if item.kind == PartKind.SYSTEM and len(_text(item.row.get("name"))) > 200:
                    raise _RejectedError(f"{item.ref}: Name länger als 200 Zeichen")
                item.components = [self._component(item, i, r) for i, r in enumerate(used, 1)]
            except _RejectedError as error:
                self._reject_item(item, str(error))

        owners: dict[str, list[tuple[_Item, int]]] = {}
        for item in self.items.values():
            for position, component in enumerate(item.components, 1):
                if component["ei"]:
                    owners.setdefault(normalize_ei(component["ei"]), []).append((item, position))

        rejected: dict[ItemKey, str] = {}
        for key, entries in owners.items():
            if len(entries) < 2:
                continue
            refs = sorted({entry.ref for entry, _ in entries})
            for item, position in entries:
                if item.archived is not None:
                    self._replace_ei(item, position, "EI kollidiert nach Normalisierung")
                elif sum(1 for other, _ in entries if other.archived is None) > 1:
                    others = ", ".join(r for r in refs if r != item.ref) or "sich selbst"
                    ei = item.components[position - 1]["ei"]
                    rejected.setdefault(
                        item.key,
                        f"{item.ref}: EI {ei!r} kollidiert nach Normalisierung ({key!r}) mit "
                        f"{others}",
                    )
        for item in list(self.items.values()):
            for position, component in enumerate(item.components, 1):
                if component["ei"]:
                    continue
                if item.archived is not None:
                    self._replace_ei(item, position, "EI fehlt")
                else:
                    where = f", Bauteil {position}" if item.kind == PartKind.SYSTEM else ""
                    rejected.setdefault(item.key, f"{item.ref}{where}: EI fehlt")
        for item_key, reason in rejected.items():
            self._reject_item(self.items[item_key], reason)

    def _replace_ei(self, item: _Item, position: int, reason: str) -> None:
        component = item.components[position - 1]
        kind = "P" if item.kind == PartKind.PRODUCT else "S"
        replacement = f"ALT-{kind}{item.legacy_id}-{position}"
        item.replaced_eis.append((position, component["ei"], replacement))
        self.report.clarifications.append(
            f"{item.ref} (ausgesondert), Bauteil {position}: {reason} "
            f"(bisher {component['ei']!r}); Ersatz-EI {replacement}"
        )
        component["ei"] = replacement

    # --- Teile anlegen ---

    def _create_parts(self) -> None:
        for item in sorted(self.items.values(), key=lambda i: (i.archived is not None, i.key)):
            table = "products" if item.kind == PartKind.PRODUCT else "systems"
            kind, legacy_id = item.kind.value, item.legacy_id
            if item.kind == PartKind.PRODUCT:
                item.part = create_product(
                    item.components[0], legacy_kind=kind, legacy_id=legacy_id
                )
            else:
                name = _text(item.row.get("name")) or f"System #{item.legacy_id}"
                item.part = create_system(
                    name, item.components, legacy_kind=kind, legacy_id=legacy_id
                )
                self._count("system_parts").imported += len(item.components)
            if item.archived is None:
                self._count(table).imported += 1
            self._attach_gal(item)
            if item.archived is None and _int(item.row.get("keine_psa")) == 1:
                record_event(item.created, EventKind.NO_PPE_SET, actor=None, comment=IMPORT_NOTE)

    def _attach_gal(self, item: _Item) -> None:
        title = f"GAL {item.created.name}"[:200]
        gal_file = _text(item.row.get("gal_datei"))
        gal_link = _text(item.row.get("gal_link"))
        if gal_file:
            document = self._gal_file(gal_file, item.ei_ref, title)
            if document is not None:
                item.created.documents.add(document)
        if gal_link:
            try:
                if len(gal_link) > 500:
                    raise InventoryError("zu lang")
                item.created.documents.add(link_document(gal_link, title=title))
            except InventoryError:
                self.report.clarifications.append(
                    f"{item.ei_ref}: GAL-Link ungültig (nur http/https), nicht übernommen"
                )

    def _gal_location(self, raw: str) -> tuple[Path | None, str]:
        """Datei im Ordner GAL/ zu einem Pfad des Altsystems, oder None mit dem Grund."""
        path = PurePosixPath(raw.replace("\\", "/"))
        if path.is_absolute() or re.match(r"^[A-Za-z]:", raw):
            return None, "GAL-Datei mit absolutem Pfad außerhalb von GAL/, nicht übernommen"
        parts = path.parts[1:] if path.parts and path.parts[0].lower() == "gal" else path.parts
        if not parts or ".." in parts:
            return None, "GAL-Pfad ungültig, nicht übernommen"
        return self.gal_dir.joinpath(*parts), "/".join(parts)

    def _mark_gal_references(self, *sources: dict[str, list[Row]]) -> None:
        """Merkt alle GAL-Dateien, auf die eine Zeile zeigt, auch bei abgelehnten Teilen."""
        for tables in sources:
            for table in ("products", "systems"):
                for row in tables.get(table, []):
                    file, _ = self._gal_location(_text(row.get("gal_datei")))
                    if file is not None and _text(row.get("gal_datei")):
                        self.used_gal_files.add(file.resolve())

    def _gal_file(self, raw: str, ref: str, title: str) -> Document | None:
        file, name = self._gal_location(raw)
        if file is None:
            self.report.clarifications.append(f"{ref}: {name}")
            return None
        if not file.is_file():
            self.report.clarifications.append(f"{ref}: GAL-Datei {name} fehlt")
            return None
        content = file.read_bytes()
        if not content.startswith(PDF_MAGIC):
            self.report.clarifications.append(f"{ref}: GAL-Datei {name} ist kein PDF")
            return None
        if not self.dry_run:
            return store_document_file(content, name, title=title)
        # Probelauf: Datensatz wie beim echten Import, aber ohne die Datei zu speichern.
        digest = hashlib.sha256(content).hexdigest()
        existing = Document.objects.filter(sha256=digest).first()
        return existing or Document.objects.create(
            title=title, sha256=digest, file=f"gal/{digest}.pdf"
        )

    def _report_orphan_gal_files(self) -> None:
        if not self.gal_dir.is_dir():
            return
        for file in sorted(p for p in self.gal_dir.rglob("*") if p.is_file()):
            if file.resolve() not in self.used_gal_files:
                name = file.relative_to(self.gal_dir).as_posix()
                self.report.clarifications.append(f"GAL-Datei {name} ohne Verweis")

    # --- Prüfstatus ---

    def _adopt_next_checks(self) -> None:
        """Übernahme-Runde: „nächste Prüfung“ jedes aktiven Teils mit gültigem Datum."""
        entries: list[tuple[_Item, date]] = []
        for item in sorted(self.items.values(), key=lambda i: i.key):
            if item.archived is not None:
                continue
            due = self._date(
                item.row.get("naechste_pruefung_am"), f"{item.ei_ref}: nächste Prüfung"
            )
            if due is not None:
                entries.append((item, due))
            elif _int(item.row.get("keine_psa")) != 1:
                self.report.clarifications.append(
                    f"{item.ei_ref}: ohne gültige nächste Prüfung, bleibt „neu, nicht freigegeben“"
                )
        if not entries:
            return
        round_ = start_round(actor=None, inspector_name=ROUND_INSPECTOR)
        for item, due in entries:
            record_result(
                round_,
                item.created,
                Outcome.PASSED,
                actor=None,
                comment=self._history(item.row),
                next_due=due,
            )
        close_round(round_, actor=None)
        adopt_legacy_round(round_, comment=IMPORT_NOTE)

    @staticmethod
    def _history(row: Row) -> str:
        """Was das Altsystem sonst zur Prüfung wusste, als Kommentar des Übernahme-Ergebnisses."""
        lines = ["Nächste Prüfung laut Altsystem."]
        if last := _text(row.get("last_psa_pruefung_am")):
            comment = _text(row.get("last_psa_pruefung_kommentar"))
            lines.append(f"Letzte Prüfung: {last}" + (f" ({comment})" if comment else ""))
        if failed := _text(row.get("last_psa_durchgefallen_am")):
            lines.append(f"Zuletzt durchgefallen: {failed}")
        return " ".join(lines)

    # --- Sperren und Aussonderung ---

    def _import_locks(self, main: dict[str, list[Row]], archive: dict[str, list[Row]]) -> None:
        rows = [(row, False) for row in main.get("item_locks", [])]
        rows += [(row, True) for row in archive.get("item_locks", [])]
        counts = self._count("item_locks")
        for row, from_archive in rows:
            counts.read += 1
            key = (_text(row.get("item_type")), _int(row.get("item_id")))
            item = self.items.get(key)
            if item is None or (item.archived is not None) != from_archive:
                counts.skipped += 1
                if item is None:
                    self.report.warnings.append(
                        f"Sperre zu {key[0]} #{key[1]}: Teil nicht übernommen, Sperre entfällt"
                    )
                continue
            locked_at = self._timestamp(row.get("lock_at"), f"{item.ei_ref}: Sperrzeitpunkt")
            unlocked_at = self._timestamp(row.get("unlock_at"), f"{item.ei_ref}: Entsperrzeitpunkt")
            if _int(row.get("is_locked")) != 1 and locked_at is None:
                counts.skipped += 1
                continue
            record_event(
                item.created,
                EventKind.LOCKED,
                actor=None,
                at=locked_at,
                comment=_text(row.get("lock_comment")) or "im Altsystem ohne Kommentar gesperrt",
            )
            if _int(row.get("is_locked")) != 1:
                at = unlocked_at or locked_at
                if locked_at is not None and at is not None and at < locked_at:
                    at = locked_at
                record_event(
                    item.created,
                    EventKind.UNLOCKED,
                    actor=None,
                    at=at,
                    comment=_text(row.get("unlock_comment"))
                    or "im Altsystem ohne Kommentar entsperrt",
                )
            counts.imported += 1

    def _timestamp(self, value: Any, what: str) -> datetime | None:
        raw = _text(value)
        if not raw:
            return None
        parsed = _parse_timestamp(raw)
        if parsed is None:
            self.report.warnings.append(f"{what} {raw!r} ungültig, Zeitpunkt der Übernahme")
        return parsed

    def _retire_archived(self) -> None:
        for item in sorted(self.items.values(), key=lambda i: i.key):
            if item.archived is None:
                continue
            entry = item.archived
            comment = _text(entry.get("kommentar"))
            note = "Im Altsystem archiviert" + (f": {comment}" if comment else "")
            at = self._timestamp(entry.get("archived_at"), f"{item.ref}: Archivierungszeitpunkt")
            record_event(item.created, EventKind.RETIRED, actor=None, at=at, comment=note)
            self._count("archived_items").imported += 1

    # --- Ausleihen ---

    def _import_loans(self, main: dict[str, list[Row]], archive: dict[str, list[Row]]) -> None:
        rows: dict[int, Row] = {}
        for row in [*archive.get("verleih_planung", []), *main.get("verleih_planung", [])]:
            rows[_int(row.get("id"))] = row  # die Hauptdatenbank gewinnt
        counts = self._count("verleih_planung")
        for legacy_id in sorted(rows):
            counts.read += 1
            self.pending_borrower = None
            try:
                with transaction.atomic():
                    self._import_loan(legacy_id, rows[legacy_id])
                counts.imported += 1
                self._remember_borrower()
            except _RejectedError as error:
                counts.rejected += 1
                self.report.rejected.append(f"Ausleihe #{legacy_id}: {error}")

    def _import_loan(self, legacy_id: int, row: Row) -> None:
        key = (_text(row.get("item_type")), _int(row.get("item_id")))
        item = self.items.get(key)
        kind = "System" if key[0] == "system" else "Produkt"
        if item is None or item.part is None:
            raise _RejectedError(f"{kind} #{key[1]} wurde nicht übernommen")
        start = _parse_date(_text(row.get("von_datum")))
        end = _parse_date(_text(row.get("rueckgabe_datum")))
        if start is None or end is None:
            raise _RejectedError("Von- oder Rückgabedatum ungültig")
        if end < start:
            raise _RejectedError("Rückgabe liegt vor dem Beginn")
        status = self._loan_status(legacy_id, row)
        if item.archived is not None and status in (LoanStatus.RESERVED, LoanStatus.LENT):
            raise _RejectedError(f"aktive Ausleihe des ausgesonderten Teils {item.ei_ref}")

        name = _text(row.get("entleiher"))
        borrower = self._borrower(legacy_id, row, name)
        created = self._timestamp(row.get("created_at"), f"Ausleihe #{legacy_id}: angelegt am")
        booking = Booking.objects.create(
            borrower=borrower, legacy_id=legacy_id, created_at=created or timezone.now()
        )
        try:
            with transaction.atomic():
                loan = Loan.objects.create(
                    booking=booking,
                    part=item.part,
                    period=DateRange(start, end, bounds="[]"),
                    status=status,
                    created_at=created or timezone.now(),
                )
        except IntegrityError as error:
            raise _RejectedError(self._overlap_reason(item, start, end)) from error

        signer = name or "unbekannt"
        if status in (LoanStatus.LENT, LoanStatus.RETURNED):
            LoanEvent.objects.create(
                loan=loan,
                kind=LoanEventKind.CHECKED_OUT,
                at=self._timestamp(row.get("checkout_at"), f"Ausleihe #{legacy_id}: Ausgabe")
                or created
                or timezone.now(),
                actor_name=self._person(row, "ausgebende_person"),
                quick_check=_int(row.get("quick_check_out")) == 1,
                gal_provided=_int(row.get("gal_provided_out")) == 1,
                signature=self._signature(legacy_id, row.get("signature_data"), signer),
            )
        if status == LoanStatus.RETURNED:
            LoanEvent.objects.create(
                loan=loan,
                kind=LoanEventKind.RETURNED,
                at=self._timestamp(row.get("returned_at"), f"Ausleihe #{legacy_id}: Rückgabe")
                or timezone.now(),
                actor_name=self._person(row, "ruecknehmende_person"),
                quick_check=_int(row.get("quick_check_return")) == 1,
                comment=_text(row.get("return_comment")),
                signature=self._signature(legacy_id, row.get("return_signature_data"), signer),
            )

    @staticmethod
    def _person(row: Row, column: str) -> str:
        name = _text(row.get(column))
        if len(name) > 200:
            raise _RejectedError("Name der ausgebenden oder rücknehmenden Person zu lang")
        return name

    def _loan_status(self, legacy_id: int, row: Row) -> LoanStatus:
        raw = _text(row.get("status")).lower()
        if raw == "returned":
            return LoanStatus.RETURNED
        if raw in LOAN_STATUSES:
            return LOAN_STATUSES[raw]
        if raw:
            raise _RejectedError(f"unbekannter Status {raw!r}")
        # Wie ensure_verleih_schema() im Verleih-Programm: mit Unterschrift verliehen.
        status = LoanStatus.LENT if _text(row.get("signature_data")) else LoanStatus.RESERVED
        self.report.warnings.append(
            f"Ausleihe #{legacy_id}: Status leer, wie im Verleih-Programm als "
            f"„{status.label}“ übernommen"
        )
        return status

    def _overlap_reason(self, item: _Item, start: date, end: date) -> str:
        other = (
            Loan.objects.filter(
                part=item.part,
                status__in=(LoanStatus.RESERVED, LoanStatus.LENT),
                period__overlap=DateRange(start, end, bounds="[]"),
            )
            .select_related("booking")
            .first()
        )
        with_ref = f" mit Ausleihe #{other.booking.legacy_id}" if other is not None else ""
        return f"überschneidet sich{with_ref} ({item.ei_ref}, beide aktiv)"

    def _borrower(self, legacy_id: int, row: Row, name: str) -> Borrower:
        email = _text(row.get("entleiher_email"))
        phone = _text(row.get("entleiher_telefon"))
        address = _text(row.get("entleiher_adresse"))
        if not name:
            self.report.warnings.append(f"Ausleihe #{legacy_id}: ohne Namen des Entleihers")
            name = "unbekannt (Altsystem)"
        if len(name) > 200 or len(email) > 254 or len(phone) > 50:
            raise _RejectedError("Kontaktdaten länger als erlaubt")
        name_key = _name_key(name)
        email_key = email.lower()
        phone_key = re.sub(r"\D", "", phone)
        for known_name, emails, phones, borrower in self.borrowers:
            if known_name == name_key and (
                (email_key and email_key in emails) or (phone_key and phone_key in phones)
            ):
                contact = BorrowerContact.objects.get(borrower=borrower)
                for attr, value in (("address", address), ("email", email), ("phone", phone)):
                    if value:
                        setattr(contact, attr, value)
                contact.save()
                self.pending_borrower = (name_key, {email_key}, {phone_key}, borrower)
                return borrower
        borrower = Borrower.objects.create()
        BorrowerContact.objects.create(
            borrower=borrower, name=name, address=address, email=email, phone=phone
        )
        self.pending_borrower = (name_key, {email_key}, {phone_key}, borrower)
        return borrower

    def _remember_borrower(self) -> None:
        """Nimmt den Entleiher der gerade übernommenen Ausleihe in die Zusammenführung auf."""
        if self.pending_borrower is None:
            return
        name_key, emails, phones, borrower = self.pending_borrower
        emails, phones = emails - {""}, phones - {""}
        for _name, known_emails, known_phones, known in self.borrowers:
            if known == borrower:
                known_emails |= emails
                known_phones |= phones
                return
        self.borrowers.append((name_key, emails, phones, borrower))

    def _signature(self, legacy_id: int, value: Any, signer: str) -> Signature | None:
        raw = _text(value)
        if not raw:
            return None
        strokes = _strokes(raw)
        if strokes is None:
            self.report.warnings.append(f"Ausleihe #{legacy_id}: Unterschrift nicht lesbar")
            return None
        return Signature.record(strokes, signer, SignatureOrigin.LEGACY)

    # --- Altberichte ---

    def _import_reports(self, main: dict[str, list[Row]], archive: dict[str, list[Row]]) -> None:
        headers: dict[int, Row] = {}
        for row in [
            *archive.get("psa_pruefungsberichte", []),
            *main.get("psa_pruefungsberichte", []),
        ]:
            headers[_int(row.get("id"))] = row
        entries: dict[int, Row] = {}
        for row in [
            *archive.get("psa_pruefungsbericht_items", []),
            *main.get("psa_pruefungsbericht_items", []),
        ]:
            entries[_int(row.get("id"))] = row

        reports: dict[int, LegacyReport] = {}
        for legacy_id in sorted(headers):
            row = headers[legacy_id]
            self._count("psa_pruefungsberichte").read += 1
            created_raw = _text(row.get("created_at"))
            reports[legacy_id] = LegacyReport.objects.create(
                legacy_id=legacy_id,
                created_at_raw=created_raw,
                created_at=_parse_timestamp(created_raw),
                inspector_name=_text(row.get("sachkundiger_name")),
                confirmed=_int(row.get("sachkundig_bestaetigt")) == 1,
                signature_data=_text(row.get("signature_data")),
            )
            self._count("psa_pruefungsberichte").imported += 1

        counts = self._count("psa_pruefungsbericht_items")
        for legacy_id in sorted(entries):
            row = entries[legacy_id]
            counts.read += 1
            report = reports.get(_int(row.get("report_id")))
            if report is None:
                counts.rejected += 1
                self.report.rejected.append(
                    f"Eintrag #{legacy_id} eines Prüfberichts: Bericht "
                    f"#{_int(row.get('report_id'))} fehlt"
                )
                continue
            item_type = _text(row.get("item_type"))
            raw_item_id = row.get("item_id")
            item_id = _int(raw_item_id) if raw_item_id is not None else None
            item = self.items.get((item_type, item_id or 0))
            LegacyReportItem.objects.create(
                report=report,
                legacy_id=legacy_id,
                item_type=item_type,
                item_id=item_id,
                item_label=_text(row.get("item_label")),
                inspection_done=_int(row.get("psa_pruefung_durchgefuehrt")) == 1,
                comment=_text(row.get("kommentar")),
                next_due_raw=_text(row.get("naechste_pruefung_am")),
                action=_text(row.get("aktion")),
                part=item.part if item is not None else None,
            )
            counts.imported += 1


def run_import(source: Path, *, dry_run: bool) -> ImportReport:
    """Importiert `source` (Ordner mit psa.db, ggf. psa_archiv.db und GAL/)."""
    return LegacyImporter(source, dry_run=dry_run).run()
