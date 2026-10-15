"""Altsystem, Dokumente und Export (Paritätszeilen 5 und 11).

Die PDFs erzeugt das Altsystem mit Typst. Typst läuft hier nicht; festgeschrieben ist, welche
Vorlage mit welchen Eingaben und welchem Dateinamen aufgerufen wird.
"""

import csv
import json
from pathlib import Path
from typing import Any

import pytest

from tests.legacy.behaviour.conftest import Legacy

FIELDS = (
    ("Produktbezeichnung", "produktbezeichnung"),
    ("gem_EN", "gem_en"),
    ("Produktname", "produktname"),
    ("Hersteller", "hersteller"),
    ("Herstellungsjahr", "herstellungsjahr"),
    ("Kaufdatum", "kaufdatum"),
    ("Datum_Einsatz", "datum_einsatz"),
    ("Einzelidentifikation", "einzelidentifikation"),
    ("Seriennummer", "seriennummer"),
)


class FakeRun:
    """Ersatz für `subprocess.run`: merkt sich den Typst-Aufruf."""

    def __init__(self) -> None:
        self.commands: list[list[str]] = []

    def __call__(self, command: list[str], **_: Any) -> Any:
        self.commands.append(command)
        return type("Result", (), {"returncode": 0, "stderr": ""})()


@pytest.fixture
def typst(legacy: Legacy, monkeypatch: pytest.MonkeyPatch) -> FakeRun:
    fake = FakeRun()
    monkeypatch.setattr(legacy.pdf, "find_typst_executable", lambda: "typst")
    monkeypatch.setattr(legacy.pdf.subprocess, "run", fake)
    return fake


def _inputs(command: list[str]) -> dict[str, str]:
    pairs = [command[i + 1] for i, arg in enumerate(command) if arg == "--input"]
    return dict(pair.split("=", 1) for pair in pairs)


@pytest.mark.parametrize(
    ("function", "template", "suffix"),
    [
        (
            "generate_einsatzdokumentation_pdf_from_db",
            "Einsatzdokumentation.typ",
            "Einsatzdokumentation",
        ),
        ("generate_pd_pdf_from_db", "PD.typ", "PD"),
        ("generate_haftungsausschluss_pdf_from_db", "Haftungsausschluss.typ", "Haftungsausschluss"),
    ],
)
def test_product_pdf_uses_template_inputs_and_ei_as_file_prefix(
    function: str, template: str, suffix: str, legacy: Legacy, typst: FakeRun, tmp_path: Path
) -> None:
    """Zeile 5: Einsatzdokumentation, PD und Haftungsausschluss für ein Produkt."""
    product_id = legacy.product("TST-PDF")

    path = getattr(legacy.pdf, function)(
        product_id=product_id, output_dir=str(tmp_path / "out"), db_path=legacy.db
    )

    assert path == tmp_path / "out" / f"TST-PDF_{suffix}.pdf"
    (command,) = typst.commands
    assert command[:3] == ["typst", "compile", str(Path("Typst_Vorlagen") / template)]
    details = legacy.psa.fetch_product_details(legacy.db, product_id)
    assert _inputs(command) == {f"t1_{label}": details[key] for label, key in FIELDS}


def test_system_pdf_uses_system_name_and_three_parts(
    legacy: Legacy, typst: FakeRun, tmp_path: Path
) -> None:
    system_id = legacy.system("Klettersteig-Set", "TST-1", "TST-2")

    path = legacy.pdf.generate_pd_pdf_from_db(
        system_id=system_id, output_dir=str(tmp_path), db_path=legacy.db
    )

    assert path.name == "Klettersteig-Set_PD.pdf"
    inputs = _inputs(typst.commands[0])
    assert inputs["systemname"] == "Klettersteig-Set"
    assert (inputs["t1_Einzelidentifikation"], inputs["t2_Einzelidentifikation"]) == (
        "TST-1",
        "TST-2",
    )
    assert inputs["t3_Einzelidentifikation"] == ""
    assert len([key for key in inputs if key.startswith("t")]) == 27


def test_pdf_fails_without_typst(legacy: Legacy, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(legacy.pdf, "find_typst_executable", lambda: None)

    with pytest.raises(RuntimeError, match="Typst wurde nicht gefunden"):
        legacy.pdf.generate_pd_pdf(inputs={})


# --- Zeile 11: Export ---


def _archive_one(legacy: Legacy, product_id: int) -> None:
    legacy.psa.archive_item_with_history(
        legacy.db,
        item_type="product",
        item_id=product_id,
        item_label="EI: TST-ALT",
        kommentar="Ablegereife",
        archived_by="Testperson Prüfer",
        archive_db_path=legacy.archive,
    )
    legacy.psa.delete_item(legacy.db, item_type="product", item_id=product_id)


def test_snapshot_of_all_items_with_archive(legacy: Legacy) -> None:
    """Zeile 11: alle Teile, mit Archiv; das Archiv liegt neben dem Programm (`psa_archiv.db`)."""
    first = legacy.product("TST-1")
    system_id = legacy.system("Set", "TST-S")
    _archive_one(legacy, legacy.product("TST-ALT"))
    legacy.loan(first, 1, 2, status="reserved")

    snapshot = legacy.psa.build_export_snapshot(legacy.db)

    assert snapshot["meta"]["scope"] == "all"
    assert [p["id"] for p in snapshot["products"]] == [first]
    assert [s["id"] for s in snapshot["systems"]] == [system_id]
    assert len(snapshot["systems"][0]["parts"]) == 3
    assert len(snapshot["verleih_planung"]) == 1
    assert [a["item_label"] for a in snapshot["archived_items"]] == ["EI: TST-ALT"]


def test_snapshot_of_selection_without_archive(legacy: Legacy) -> None:
    """Zeile 11: markierte Teile, ohne Archiv."""
    first, second = legacy.product("TST-1"), legacy.product("TST-2")
    legacy.system("Set", "TST-S")
    legacy.loan(second, 1, 2, status="reserved")
    _archive_one(legacy, legacy.product("TST-ALT"))

    snapshot = legacy.psa.build_export_snapshot(
        legacy.db, selected_products={first}, include_archive=False
    )

    assert snapshot["meta"]["scope"] == "selected"
    assert [p["id"] for p in snapshot["products"]] == [first]
    assert snapshot["systems"] == []
    assert snapshot["verleih_planung"] == []
    assert snapshot["archived_items"] == []


def test_csv_has_one_row_per_record_with_json_data(legacy: Legacy, tmp_path: Path) -> None:
    """Zeile 11: CSV mit Abschnitt, Typ, ID, Elternteil, Beschriftung und JSON-Daten."""
    legacy.product("TST-1")
    legacy.system("Set", "TST-S")
    snapshot = legacy.psa.build_export_snapshot(legacy.db, include_archive=False)

    path = legacy.psa.export_snapshot_to_csv(snapshot, tmp_path / "export.csv")

    raw = path.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")  # UTF-8 mit BOM für Excel
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert list(rows[0]) == ["section", "item_type", "item_id", "parent_id", "label", "data"]
    sections = [row["section"] for row in rows]
    assert sections.count("meta") == 5
    assert sections.count("products") == 1
    assert sections.count("systems") == 1
    assert sections.count("system_parts") == 3
    product = next(row for row in rows if row["section"] == "products")
    assert json.loads(product["data"])["einzelidentifikation"] == "TST-1"
    assert product["label"] == "Testmodell A1"
