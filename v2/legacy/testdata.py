"""Testdaten-Generator: künstliche Datenbanken im Format des Altsystems.

Erzeugt `psa.db`, bei Varianten mit Archiv zusätzlich `psa_archiv.db`, und den Ordner `GAL/`
mit Platzhalter-PDFs. Die Daten verhalten sich wie die des Altsystems: gleiche Tabellen und
Spalten je Schema-Variante, gleiche Formate für Datum, Zeitstempel, Status, Unterschriften und
Beschriftungen. Bei Varianten mit Archiv werden drei Teile wie in `psa_gui.py` ausgesondert:
in die Archivdatenbank kopiert und aus der Hauptdatenbank gelöscht, wobei ihre Ausleihen und
Prüfberichte in der Hauptdatenbank stehen bleiben.

Alle Inhalte sind erkennbar künstlich: Nachnamen wie „Beispiel“ oder „Muster“, E-Mail-Adressen
unter example.org, Telefonnummern mit der Vorwahl 0000, Hersteller und Modelle frei erfunden.
Gleicher Startwert und gleicher Stichtag ergeben dieselben Daten.

Mit `edge_cases=True` (Standard) kommen Randfälle dazu, die in echten Altdaten vorkommen können
und an denen sich der Import beweisen muss: EI-Dubletten, ungewöhnliche Datumsangaben,
überfällige und überschneidende Ausleihen, leerer Status, unvollständige Systeme und fehlende
oder verwaiste GAL-Dateien.

Echte Altdaten liest dieses Modul nie (CLAUDE.md, Regel 1). Vorhandene Dateien werden nicht
überschrieben.
"""

import json
import random
import sqlite3
from collections.abc import Iterable
from contextlib import closing
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path

from legacy.schema import VARIANTS, SchemaVariant, create_tables

MAIN_DB_NAME = "psa.db"
ARCHIVE_DB_NAME = "psa_archiv.db"
GAL_DIR_NAME = "GAL"
DEFAULT_REFERENCE_DATE = date(2026, 10, 1)

# Platzhalter statt echter Gebrauchsanleitungen.
PLACEHOLDER_PDF = b"%PDF-1.4\n% Platzhalter-GAL, kuenstliche Testdaten\n%%EOF\n"

FIRST_NAMES = (
    "Alex",
    "Bea",
    "Chris",
    "Dana",
    "Eli",
    "Fritzi",
    "Gabi",
    "Hanne",
    "Ilo",
    "Jo",
    "Kim",
    "Lou",
    "Mika",
    "Noa",
    "Robin",
    "Sascha",
    "Toni",
    "Uli",
)
LAST_NAMES = ("Beispiel", "Muster", "Probe", "Testmann", "Exempel", "Platzhalter")
STREETS = ("Teststraße", "Beispielweg", "Musterallee", "Probegasse")
CITY = "00000 Beispielstadt"

PRODUCT_TYPES = (
    ("Klettergurt", "EN 12277"),
    ("Kletterseil 50 m", "EN 892"),
    ("Karabiner HMS", "EN 12275"),
    ("Bandschlinge 120 cm", "EN 566"),
    ("Kletterhelm", "EN 12492"),
    ("Expressschlinge", "EN 12275"),
    ("Sicherungsgerät", "EN 15151-1"),
    ("Abseilachter", "EN 15151-2"),
    ("Klettersteigset", "EN 958"),
    ("Reepschnur 6 mm", "EN 564"),
)
MANUFACTURERS = ("Beispiel Bergsport", "Muster Alpin", "Testwerk Outdoor", "Probe Climbing")
SYSTEM_KINDS = (
    ("Gurt-Set", ("Klettergurt", "Karabiner HMS", "Sicherungsgerät")),
    ("Klettersteig-Paket", ("Klettergurt", "Klettersteigset", "Kletterhelm")),
    ("Abseil-Set", ("Abseilachter", "Karabiner HMS", "Reepschnur 6 mm")),
)
CHECK_COMMENTS = (
    "ohne Befund",
    "Nähte und Gurtbänder geprüft",
    "Schnapper gängig",
    "leichte Abriebspuren, in Ordnung",
    "Mantel geprüft",
)
LOCK_COMMENTS = (
    "Sichtprüfung auffällig",
    "Sturzbelastung gemeldet",
    "Rückruf des Herstellers prüfen",
)
UNLOCK_COMMENTS = ("Nachprüfung ohne Befund", "Rückruf betrifft dieses Teil nicht")
RETURN_COMMENTS = ("", "alles in Ordnung", "Seil verschmutzt zurück", "Karabiner leicht verkratzt")

N_PRODUCTS = 24
N_SYSTEMS = 6
N_LOANS = 18
N_LOCKS = 4
N_REPORTS = 4

Row = dict[str, object]


@dataclass(frozen=True)
class GeneratedLegacyData:
    """Ergebnis eines Generatorlaufs: Pfade der erzeugten Dateien."""

    variant: SchemaVariant
    main_db: Path
    archive_db: Path | None
    gal_dir: Path


@dataclass
class _World:
    """Alle Zeilen beider Datenbanken, bevor sie je nach Variante geschrieben werden."""

    products: list[Row] = field(default_factory=list)
    systems: list[Row] = field(default_factory=list)
    system_parts: list[Row] = field(default_factory=list)
    item_locks: list[Row] = field(default_factory=list)
    loans: list[Row] = field(default_factory=list)
    reports: list[Row] = field(default_factory=list)
    report_items: list[Row] = field(default_factory=list)
    archive: dict[str, list[Row]] = field(default_factory=dict)
    gal_files: set[str] = field(default_factory=set)


class _Builder:
    """Baut die künstlichen Daten. Jeder Zufall kommt aus einem Generator mit Startwert."""

    def __init__(self, seed: int, reference_date: date, edge_cases: bool) -> None:
        # Zufall nur für Testdaten, keine Kryptografie.
        self.rng = random.Random(seed)  # noqa: S311
        self.today = reference_date
        self.edge_cases = edge_cases
        self.world = _World()
        self._next_part_id = 1
        self._next_lock_id = 1
        self._next_loan_id = 1
        self._next_report_id = 1
        self._next_report_item_id = 1

    # --- Hilfen für Werte ---

    def person(self) -> str:
        return f"{self.rng.choice(FIRST_NAMES)} {self.rng.choice(LAST_NAMES)}"

    def email(self, name: str) -> str:
        first, last = name.lower().split(" ", 1)
        return f"{first}.{last}@example.org"

    def phone(self) -> str:
        return f"0000 {self.rng.randint(100000, 999999)}"

    def address(self) -> str:
        return f"{self.rng.choice(STREETS)} {self.rng.randint(1, 99)}, {CITY}"

    def day(self, offset: int) -> date:
        return self.today + timedelta(days=offset)

    def timestamp(self, day: date) -> str:
        moment = time(self.rng.randint(8, 19), self.rng.randint(0, 59), self.rng.randint(0, 59))
        return datetime.combine(day, moment).isoformat(timespec="seconds")

    def signature(self) -> str:
        strokes = []
        for _ in range(self.rng.randint(2, 4)):
            x, y = self.rng.randint(10, 200), self.rng.randint(10, 80)
            stroke = []
            for _ in range(self.rng.randint(5, 12)):
                x += self.rng.randint(-6, 12)
                y += self.rng.randint(-8, 8)
                stroke.append([x, y])
            strokes.append(stroke)
        return json.dumps(strokes)

    def gal_path(self, model: str, kind: str) -> str:
        # Das Altsystem lief unter Windows und speichert relative Pfade in GAL\.
        filename = f"{model}_GAL_{kind.split()[0]}.pdf"
        self.world.gal_files.add(filename)
        return f"{GAL_DIR_NAME}\\{filename}"

    def psa_fields(self, keine_psa: bool) -> Row:
        if keine_psa:
            return {"keine_psa": 1, "naechste_pruefung_am": None}
        next_check = self.day(self.rng.randint(-45, 330))
        last_check = next_check - timedelta(days=365)
        row: Row = {
            "keine_psa": 0,
            "naechste_pruefung_am": next_check.isoformat(),
            "last_psa_pruefung_am": self.timestamp(last_check),
            "last_psa_pruefung_kommentar": self.rng.choice(CHECK_COMMENTS),
        }
        if self.rng.random() < 0.1:
            row["last_psa_durchgefallen_am"] = self.timestamp(last_check - timedelta(days=30))
        return row

    def component(self, kind: str, en: str, ei: str) -> Row:
        # Kauf und erster Einsatz liegen vor dem Stichtag, hergestellt im Kaufjahr oder davor.
        bought = self.day(-self.rng.randint(60, 8 * 365))
        first_use = min(bought + timedelta(days=self.rng.randint(0, 120)), self.day(-1))
        return {
            "produktbezeichnung": kind,
            "gem_en": en,
            "produktname": f"Testmodell {self.rng.choice('ABCDEFGH')}{self.rng.randint(1, 9)}",
            "hersteller": self.rng.choice(MANUFACTURERS),
            "herstellungsjahr": str(bought.year - self.rng.randint(0, 1)),
            "kaufdatum": bought.isoformat(),
            "datum_einsatz": first_use.isoformat(),
            "einzelidentifikation": ei,
            "seriennummer": f"SN-TEST-{self.rng.randint(10000, 99999)}",
        }

    # --- Stammdaten ---

    def add_product(self, ei: str, **overrides: object) -> Row:
        kind, en = self.rng.choice(PRODUCT_TYPES)
        row: Row = {"id": len(self.world.products) + 1, **self.component(kind, en, ei)}
        row["gal_datei"] = self.gal_path(str(row["produktname"]), kind)
        row["gal_link"] = ""
        row.update(self.psa_fields(keine_psa=self.rng.random() < 0.1))
        row.update(overrides)
        self.world.products.append(row)
        return row

    def add_system(self, number: int, parts: int = 3, **overrides: object) -> Row:
        name, kinds = self.rng.choice(SYSTEM_KINDS)
        system_id = len(self.world.systems) + 1
        row: Row = {
            "id": system_id,
            "name": f"{name} {number}",
            "gal_datei": self.gal_path(f"System{system_id}", name),
            "gal_link": "",
        }
        row.update(self.psa_fields(keine_psa=False))
        row.update(overrides)
        self.world.systems.append(row)
        for index in range(1, parts + 1):
            kind = kinds[index - 1]
            en = dict(PRODUCT_TYPES)[kind]
            part: Row = {
                "id": self._next_part_id,
                "system_id": system_id,
                "part_index": index,
                **self.component(kind, en, f"TST-S{system_id:02d}-{index}"),
            }
            self._next_part_id += 1
            self.world.system_parts.append(part)
        return row

    def first_part(self, system_id: object) -> Row | None:
        for part in self.world.system_parts:
            if part["system_id"] == system_id and part["part_index"] == 1:
                return part
        return None

    def label(self, item_type: str, item: Row) -> str:
        # Gleiches Format wie refresh_verleih_item_label() in psa_gui.py.
        if item_type == "product":
            return (
                f"EI: {item.get('einzelidentifikation') or '-'} | "
                f"PB: {item.get('produktbezeichnung') or '-'} | "
                f"Name: {item.get('produktname') or 'Ohne Name'} | "
                f"SN: {item.get('seriennummer') or '-'}"
            )
        part = self.first_part(item["id"])
        ei = part.get("einzelidentifikation") if part else None
        return f"EI: {ei or '-'} | Name: {item.get('name') or 'System'}"

    def random_item(self) -> tuple[str, Row]:
        if self.rng.random() < 0.75:
            return "product", self.rng.choice(self.world.products)
        return "system", self.rng.choice(self.world.systems)

    # --- Vorgänge ---

    def add_lock(self, item_type: str, item: Row, *, still_locked: bool) -> None:
        if any(
            lock["item_type"] == item_type and lock["item_id"] == item["id"]
            for lock in self.world.item_locks
        ):
            return  # item_locks hat UNIQUE(item_type, item_id)
        locked_on = self.day(-self.rng.randint(5, 200))
        row: Row = {
            "id": self._next_lock_id,
            "item_type": item_type,
            "item_id": item["id"],
            "is_locked": 1 if still_locked else 0,
            "lock_comment": self.rng.choice(LOCK_COMMENTS),
            "lock_at": self.timestamp(locked_on),
            "unlock_comment": None,
            "unlock_at": None,
            "unlock_source": None,
        }
        if not still_locked:
            row["unlock_comment"] = self.rng.choice(UNLOCK_COMMENTS)
            row["unlock_at"] = self.timestamp(locked_on + timedelta(days=self.rng.randint(1, 20)))
            row["unlock_source"] = "psa_gui"
        self._next_lock_id += 1
        self.world.item_locks.append(row)

    def add_loan(
        self, item_type: str, item: Row, *, start: int, days: int, status: str, **overrides: object
    ) -> Row:
        von = self.day(start)
        rueck = von + timedelta(days=days)
        borrower = self.person()
        issuer = self.person()
        row: Row = {
            "id": self._next_loan_id,
            "item_type": item_type,
            "item_id": item["id"],
            "item_label": self.label(item_type, item),
            "von_datum": von.isoformat(),
            "rueckgabe_datum": rueck.isoformat(),
            "entleiher": borrower,
            "ausgebende_person": issuer,
            "entleiher_email": self.email(borrower),
            "entleiher_telefon": self.phone(),
            "entleiher_adresse": self.address(),
            "signature_data": "",
            "quick_check_out": 0,
            "gal_provided_out": 0,
            "status": status,
            "checkout_at": None,
            "return_comment": None,
            "ruecknehmende_person": None,
            "return_signature_data": None,
            "quick_check_return": 0,
            "returned_at": None,
            "created_at": self.timestamp(von - timedelta(days=self.rng.randint(1, 30))),
        }
        if status in ("lent", "returned"):
            row["signature_data"] = self.signature()
            row["quick_check_out"] = 1
            row["gal_provided_out"] = 1
            row["checkout_at"] = self.timestamp(von)
        if status == "returned":
            row["return_comment"] = self.rng.choice(RETURN_COMMENTS)
            row["ruecknehmende_person"] = self.person()
            row["return_signature_data"] = self.signature()
            row["quick_check_return"] = 1
            row["returned_at"] = self.timestamp(rueck)
        row.update(overrides)
        self._next_loan_id += 1
        self.world.loans.append(row)
        return row

    def is_booked(self, item_type: str, item: Row, start: int, days: int) -> bool:
        """True, wenn eine aktive Ausleihe des Teils den Zeitraum berührt (beide Tage inklusive)."""
        von, rueck = self.day(start).isoformat(), self.day(start + days).isoformat()
        return any(
            loan["item_type"] == item_type
            and loan["item_id"] == item["id"]
            and loan["status"] in ("reserved", "lent")
            and str(loan["von_datum"]) <= rueck
            and str(loan["rueckgabe_datum"]) >= von
            for loan in self.world.loans
        )

    def has_active_loans(self, item_type: str, item: Row) -> bool:
        return any(
            loan["item_type"] == item_type
            and loan["item_id"] == item["id"]
            and loan["status"] in ("reserved", "lent")
            for loan in self.world.loans
        )

    def add_report(self, entries: Iterable[tuple[str, Row, str]], days_ago: int) -> Row:
        report: Row = {
            "id": self._next_report_id,
            "created_at": self.timestamp(self.day(-days_ago)),
            "sachkundiger_name": self.person(),
            "sachkundig_bestaetigt": 1,
            "signature_data": self.signature(),
        }
        self._next_report_id += 1
        self.world.reports.append(report)
        for item_type, item, action in entries:
            next_check = self.day(365 - days_ago).isoformat()
            self.world.report_items.append(
                {
                    "id": self._next_report_item_id,
                    "report_id": report["id"],
                    "item_type": item_type,
                    "item_id": item["id"],
                    "item_label": self.label(item_type, item),
                    "psa_pruefung_durchgefuehrt": 0 if action == "archived+delete" else 1,
                    "kommentar": self.rng.choice(CHECK_COMMENTS),
                    "naechste_pruefung_am": None if action == "archived+delete" else next_check,
                    "aktion": action,
                }
            )
            self._next_report_item_id += 1
        return report

    # --- Aufbau ---

    def build(self) -> _World:
        for number in range(1, N_PRODUCTS + 1):
            self.add_product(f"TST-{number:04d}")
        for number in range(1, N_SYSTEMS + 1):
            self.add_system(number)
        if self.edge_cases:
            self.add_master_data_edge_cases()

        for _ in range(N_LOCKS):
            item_type, item = self.random_item()
            self.add_lock(item_type, item, still_locked=self.rng.random() < 0.5)

        for _ in range(N_LOANS):
            status = self.rng.choice(("returned", "returned", "lent", "reserved"))
            if status == "returned":
                start = -self.rng.randint(20, 300)
            elif status == "lent":
                start = -self.rng.randint(0, 5)
            else:
                start = self.rng.randint(3, 60)
            days = self.rng.randint(1, 10)
            # Wie im Altsystem: Vor dem Speichern prüft es, ob das Teil im Zeitraum frei ist.
            for _ in range(20):
                item_type, item = self.random_item()
                if status == "returned" or not self.is_booked(item_type, item, start, days):
                    break
            else:
                continue
            self.add_loan(item_type, item, start=start, days=days, status=status)
        if self.edge_cases:
            self.add_loan_edge_cases()

        for number in range(N_REPORTS):
            entries = []
            for _ in range(self.rng.randint(3, 7)):
                item_type, item = self.random_item()
                action = self.rng.choice(
                    ("update", "update", "update", "lock+update", "unlock+update")
                )
                entries.append((item_type, item, action))
            self.add_report(entries, days_ago=30 + number * 90)

        self.archive_items()
        return self.world

    def add_master_data_edge_cases(self) -> None:
        # EI-Dubletten: exakt (vor a39ebdf möglich), Großschreibung, Leerzeichen innen und
        # außen. Das Altsystem vergleicht LOWER(TRIM(...)); PSAgent 2.0 ignoriert zusätzlich
        # Leerzeichen innen.
        for ei in ("TST-0900", "TST-0900", "TST-0901", "tst-0901", "TST 0902", "TST0902"):
            self.add_product(ei)
        self.add_product(" TST-0903")
        self.add_product("TST-0903 ")
        self.add_product("TST-0904")
        self.add_product("")
        # Ungewöhnliche Datumsangaben aus Freitextfeldern
        self.add_product("TST-0910", kaufdatum="01.03.2021", datum_einsatz="")
        self.add_product("TST-0911", kaufdatum="2021-02-30", herstellungsjahr="unbekannt")
        self.add_product("TST-0912", naechste_pruefung_am="31.12.2026")
        # „keine PSA“ und trotzdem ein Prüftermin
        self.add_product("TST-0913", keine_psa=1, naechste_pruefung_am=self.day(90).isoformat())
        # GAL: Datei fehlt, nur Link, Schrägstrich statt Backslash, absoluter Windows-Pfad
        self.add_product("TST-0914", gal_datei=f"{GAL_DIR_NAME}\\Fehlt_GAL.pdf")
        self.add_product(
            "TST-0915", gal_datei="", gal_link="https://example.org/gal/testmodell.pdf"
        )
        product = self.add_product("TST-0916")
        product["gal_datei"] = str(product["gal_datei"]).replace("\\", "/")
        self.add_product("TST-0917", gal_datei="C:\\Users\\Beispiel\\Downloads\\gal.pdf")

        # Systeme: EI kollidiert mit Produkt, Teile ohne Angaben, nur ein Teil, gar keine EI
        self.add_system(90)
        self.world.system_parts[-1]["einzelidentifikation"] = "TST-0904"
        system = self.add_system(91)
        for part in self.world.system_parts:
            if part["system_id"] == system["id"] and part["part_index"] != 1:
                for key in list(part):
                    if key not in ("id", "system_id", "part_index"):
                        part[key] = ""
        self.add_system(92, parts=1)
        system = self.add_system(93)
        for part in self.world.system_parts:
            if part["system_id"] == system["id"]:
                part["einzelidentifikation"] = ""

    def add_loan_edge_cases(self) -> None:
        product = self.world.products[0]
        # Überfällig: verliehen, Rückgabe liegt in der Vergangenheit
        self.add_loan("product", product, start=-20, days=7, status="lent")
        # Überschneidung mit einer Reservierung für dasselbe Teil
        other = self.world.products[1]
        self.add_loan("product", other, start=-2, days=10, status="lent")
        self.add_loan("product", other, start=4, days=5, status="reserved")
        # Leerer Status: verleih_gui.py setzt ihn beim Start auf reserved bzw. lent
        self.add_loan("product", self.world.products[2], start=10, days=3, status="")
        self.add_loan("product", self.world.products[3], start=-1, days=3, status="lent")
        self.world.loans[-1]["status"] = ""
        # Rückgabe nach der nächsten PSA-Prüfung
        item = self.world.products[4]
        item["naechste_pruefung_am"] = self.day(5).isoformat()
        item["keine_psa"] = 0
        self.add_loan("product", item, start=1, days=10, status="reserved")
        # Ausleihe eines gesperrten Systems
        system = self.world.systems[0]
        self.add_lock("system", system, still_locked=True)
        self.add_loan("system", system, start=7, days=2, status="reserved")

    def archive_items(self) -> None:
        """Sondert zwei Produkte und ein System ohne aktive Ausleihen aus, wie im Altsystem.

        Die Teile bekommen vorher eine Geschichte (Ausleihe, Sperre, Prüfbericht). Beim
        Archivieren kopiert das Altsystem Teil, Ausleihen, Sperre und betroffene Prüfberichte
        in die Archivdatenbank, legt einen Eintrag in archived_items an und löscht danach
        Teil, Bauteile und Sperre aus der Hauptdatenbank. Ausleihen und Prüfberichte bleiben in
        der Hauptdatenbank stehen und verweisen dann auf ein Teil, das es dort nicht mehr gibt.
        """
        # Wie im Altsystem: Archivieren nur ohne aktive Reservierung oder Ausleihe. Die ersten
        # Teile sind für Randfälle der Ausleihen reserviert.
        products = [p for p in self.world.products[5:] if not self.has_active_loans("product", p)]
        systems = [s for s in self.world.systems[1:] if not self.has_active_loans("system", s)]
        chosen = [("product", products[0]), ("product", products[1]), ("system", systems[0])]
        for item_type, item in chosen:
            self.add_loan(item_type, item, start=-400, days=5, status="returned")
            self.add_lock(item_type, item, still_locked=True)
        self.add_report([(t, i, "update") for t, i in chosen], days_ago=420)
        final = self.add_report([(t, i, "archived+delete") for t, i in chosen], days_ago=10)

        archive: dict[str, list[Row]] = {
            "products": [],
            "systems": [],
            "system_parts": [],
            "item_locks": [],
            "verleih_planung": [],
            "psa_pruefungsberichte": [],
            "psa_pruefungsbericht_items": [],
            "archived_items": [],
        }
        for item_type, item in chosen:
            item_id = item["id"]
            if item_type == "product":
                archive["products"].append(dict(item))
            else:
                archive["systems"].append(dict(item))
                archive["system_parts"] += [
                    dict(p) for p in self.world.system_parts if p["system_id"] == item_id
                ]
            matches = [
                r
                for r in self.world.loans
                if r["item_type"] == item_type and r["item_id"] == item_id
            ]
            archive["verleih_planung"] += [dict(r) for r in matches]
            archive["item_locks"] += [
                dict(r)
                for r in self.world.item_locks
                if r["item_type"] == item_type and r["item_id"] == item_id
            ]
            report_ids = {
                r["report_id"]
                for r in self.world.report_items
                if r["item_type"] == item_type and r["item_id"] == item_id
            }
            for report in self.world.reports:
                if report["id"] in report_ids and report not in archive["psa_pruefungsberichte"]:
                    archive["psa_pruefungsberichte"].append(dict(report))
                    archive["psa_pruefungsbericht_items"] += [
                        dict(r) for r in self.world.report_items if r["report_id"] == report["id"]
                    ]
            archive["archived_items"].append(
                {
                    "archived_item_id": len(archive["archived_items"]) + 1,
                    "item_type": item_type,
                    "source_item_id": item_id,
                    "item_label": self.label(item_type, item),
                    "archived_at": final["created_at"],
                    "archived_by": final["sachkundiger_name"],
                    "kommentar": "Ausgesondert nach PSA-Prüfung",
                }
            )
        self.world.archive = archive

    def remove_archived_from_main(self) -> None:
        """Löscht archivierte Teile aus der Hauptdatenbank, wie delete_item() im Altsystem."""
        for entry in self.world.archive.get("archived_items", []):
            item_type, item_id = entry["item_type"], entry["source_item_id"]
            if item_type == "product":
                self.world.products = [p for p in self.world.products if p["id"] != item_id]
            else:
                self.world.systems = [s for s in self.world.systems if s["id"] != item_id]
                self.world.system_parts = [
                    p for p in self.world.system_parts if p["system_id"] != item_id
                ]
            self.world.item_locks = [
                lock
                for lock in self.world.item_locks
                if not (lock["item_type"] == item_type and lock["item_id"] == item_id)
            ]


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _insert(conn: sqlite3.Connection, table: str, rows: Iterable[Row]) -> None:
    """Schreibt Zeilen, beschränkt auf die Spalten, die die Tabelle in dieser Variante hat."""
    columns = [row[1] for row in conn.execute(f"PRAGMA table_info({_quote(table)})")]
    if not columns:
        return  # Tabelle gibt es in dieser Variante nicht
    for row in rows:
        names = [column for column in columns if column in row]
        # Tabellen- und Spaltennamen stammen aus dem Schema, nie aus Eingaben.
        sql = (
            f"INSERT INTO {_quote(table)} ({', '.join(_quote(n) for n in names)}) "  # noqa: S608
            f"VALUES ({', '.join('?' for _ in names)})"
        )
        conn.execute(sql, [row[name] for name in names])


def _write_db(path: Path, statements: tuple[str, ...], tables: dict[str, list[Row]]) -> None:
    with closing(sqlite3.connect(path)) as conn, conn:
        create_tables(conn, statements)
        for table, rows in tables.items():
            _insert(conn, table, rows)


def build_legacy_db(
    target_dir: Path,
    variant: str | SchemaVariant,
    seed: int,
    *,
    edge_cases: bool = True,
    reference_date: date = DEFAULT_REFERENCE_DATE,
) -> GeneratedLegacyData:
    """Erzeugt eine künstliche Altsystem-Datenbank der angegebenen Schema-Variante.

    `target_dir` wird bei Bedarf angelegt. Liegen dort schon `psa.db`, `psa_archiv.db` oder
    ein Ordner `GAL/`, bricht die Funktion mit FileExistsError ab und schreibt nichts.
    """
    schema = VARIANTS[variant] if isinstance(variant, str) else variant
    main_db = target_dir / MAIN_DB_NAME
    archive_db = target_dir / ARCHIVE_DB_NAME
    gal_dir = target_dir / GAL_DIR_NAME
    existing = [path.name for path in (main_db, archive_db, gal_dir) if path.exists()]
    if existing:
        raise FileExistsError(
            f"Im Zielordner {target_dir} liegt bereits {', '.join(existing)}. "
            "Der Generator überschreibt nichts; bitte einen leeren Ordner angeben."
        )

    builder = _Builder(seed, reference_date, edge_cases)
    world = builder.build()
    if schema.has_archive:
        builder.remove_archived_from_main()
    else:
        world.archive = {}  # Ohne Archivdatenbank gibt es im Altsystem kein Archivieren.

    target_dir.mkdir(parents=True, exist_ok=True)
    _write_db(
        main_db,
        schema.main,
        {
            "products": world.products,
            "systems": world.systems,
            "system_parts": world.system_parts,
            "item_locks": world.item_locks,
            "verleih_planung": world.loans,
            "psa_pruefungsberichte": world.reports,
            "psa_pruefungsbericht_items": world.report_items,
        },
    )
    if schema.has_archive:
        _write_db(archive_db, schema.archive, world.archive)

    gal_dir.mkdir()
    for filename in sorted(world.gal_files):
        (gal_dir / filename).write_bytes(PLACEHOLDER_PDF)

    return GeneratedLegacyData(
        variant=schema,
        main_db=main_db,
        archive_db=archive_db if schema.has_archive else None,
        gal_dir=gal_dir,
    )
