"""Gemeinsame Hilfen für die Tests, die das Verhalten des Altsystems festschreiben (#9).

Jeder Test bekommt frische Datenbanken in einem eigenen Ordner, angelegt so, wie das PSA- und
das Verleih-Programm es beim Start tun. Der Altsystem-Code läuft unverändert, tkinter ist durch
ein leeres Ersatzmodul ersetzt (`tests/legacy/reference.py`). Alle Daten sind künstlich.
"""

import sqlite3
from collections.abc import Iterator
from contextlib import closing
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from tests.legacy.reference import export_legacy_code, legacy_program, start_psa, start_verleih

SIGNATURE = "[[[10, 10], [20, 15], [30, 12]]]"


@pytest.fixture(scope="session")
def legacy_code(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Arbeitsstand des Altsystems, einmal in einen Testordner kopiert."""
    return export_legacy_code(tmp_path_factory.mktemp("altsystem-code"))


@dataclass
class Legacy:
    """Geladene Altsystem-Module und die Datenbanken eines Tests."""

    psa: ModuleType
    verleih: ModuleType
    pdf: ModuleType
    db: Path
    archive: Path

    def rows(
        self, sql: str, params: tuple[Any, ...] = (), *, archive: bool = False
    ) -> list[dict[str, Any]]:
        with closing(sqlite3.connect(self.archive if archive else self.db)) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(row) for row in conn.execute(sql, params).fetchall()]

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute(sql, params)

    def today(self) -> date:
        """Heute aus Sicht von SQLite: `DATE('now')` rechnet in UTC."""
        return date.fromisoformat(self.rows("SELECT DATE('now') AS d")[0]["d"])

    def day(self, offset: int) -> str:
        return (self.today() + timedelta(days=offset)).isoformat()

    def product(self, ei: str = "TST-0001", **fields: Any) -> int:
        """Legt ein Produkt wie das PSA-Programm an; Standard: PSA in einem Jahr fällig."""
        data = {
            "produktbezeichnung": "Klettergurt",
            "gem_en": "EN 12277",
            "produktname": "Testmodell A1",
            "hersteller": "Beispiel Bergsport",
            "herstellungsjahr": "2022",
            "kaufdatum": "2023-03-01",
            "datum_einsatz": "2023-04-01",
            "einzelidentifikation": ei,
            "seriennummer": "SN-TEST-1",
            "naechste_pruefung_am": self.day(365),
            **fields,
        }
        return int(self.psa.insert_product(self.db, data))

    def system(self, name: str = "Gurt-Set", *eis: str, **fields: Any) -> int:
        """Legt ein System mit drei Teilen an, wie `save_system` im PSA-Programm."""
        system_id = int(
            self.psa.insert_system(
                self.db,
                name,
                fields.get("gal_datei", ""),
                fields.get("gal_link", ""),
                fields.get("keine_psa", False),
                fields.get("naechste_pruefung_am", self.day(365)),
            )
        )
        for index in (1, 2, 3):
            ei = eis[index - 1] if index <= len(eis) else ""
            part = {"produktbezeichnung": f"Teil {index}", "einzelidentifikation": ei} if ei else {}
            self.psa.insert_system_part(self.db, system_id, index, part)
        return system_id

    def loan(
        self,
        item_id: int,
        start: int,
        end: int,
        *,
        item_type: str = "product",
        status: str = "lent",
        **fields: Any,
    ) -> int:
        """Legt eine Ausleihe wie das Verleih-Programm an; Tage relativ zu heute."""
        data: dict[str, Any] = {
            "item_type": item_type,
            "item_id": item_id,
            "item_label": f"EI: Test | {item_type} #{item_id}",
            "von_datum": self.day(start),
            "rueckgabe_datum": self.day(end),
            "entleiher": "Kim Beispiel",
            "ausgebende_person": "Testperson Muster",
            "entleiher_email": "kim.beispiel@example.org",
            "entleiher_telefon": "0000 123456",
            "entleiher_adresse": "Teststraße 1, 00000 Beispielstadt",
            "signature_data": SIGNATURE if status == "lent" else "",
            "quick_check_out": status == "lent",
            "gal_provided_out": status == "lent",
            "status": status,
            "checkout_at": "2026-01-01T10:00:00" if status == "lent" else None,
            **fields,
        }
        return int(self.verleih.insert_verleih(self.db, **data))

    def available(self, item_id: int, start: int, end: int, item_type: str = "product") -> bool:
        return bool(
            self.verleih.is_item_available(
                self.db,
                item_type=item_type,
                item_id=item_id,
                von_datum=self.day(start),
                bis_datum=self.day(end),
            )
        )


@pytest.fixture
def legacy(legacy_code: Path, tmp_path: Path) -> Iterator[Legacy]:
    """Frische Datenbanken, beide Programme einmal gestartet; Arbeitsordner = Testordner."""
    with legacy_program(legacy_code, tmp_path) as modules:
        db = tmp_path / "psa.db"
        start_psa(modules, db)
        start_verleih(modules, db)
        yield Legacy(
            psa=modules["psa_gui"],
            verleih=modules["verleih_gui"],
            pdf=modules["generate_PDF"],
            db=db,
            archive=tmp_path / "psa_archiv.db",
        )
