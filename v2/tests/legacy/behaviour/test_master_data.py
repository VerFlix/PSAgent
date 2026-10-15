"""Altsystem, Stammdaten (Paritätszeilen 1 bis 4): so verhält sich `psa_gui.py` heute.

Die Prüfungen „EI Pflicht“ und „mindestens eine EI je System“ stehen nur in der Oberfläche
(`save_product`, `save_system`); festgeschrieben ist hier, was die Datenbankfunktionen tun.
"""

import pytest

from tests.legacy.behaviour.conftest import Legacy


def test_insert_product_stores_nine_fields_next_check_gal_and_no_ppe(legacy: Legacy) -> None:
    """Zeile 1: 9 Felder, nächste Prüfung, GAL-Datei/-Link, „keine PSA“."""
    product_id = legacy.product(
        "TST-0100",
        gal_datei="GAL\\Testmodell A1_GAL.pdf",
        gal_link="https://example.org/gal.pdf",
        keine_psa=True,
        naechste_pruefung_am="2027-04-01",
    )

    details = legacy.psa.fetch_product_details(legacy.db, product_id)

    assert details == {
        "id": product_id,
        "produktbezeichnung": "Klettergurt",
        "gem_en": "EN 12277",
        "produktname": "Testmodell A1",
        "hersteller": "Beispiel Bergsport",
        "herstellungsjahr": "2022",
        "kaufdatum": "2023-03-01",
        "datum_einsatz": "2023-04-01",
        "einzelidentifikation": "TST-0100",
        "seriennummer": "SN-TEST-1",
        "gal_datei": "GAL\\Testmodell A1_GAL.pdf",
        "gal_link": "https://example.org/gal.pdf",
        "keine_psa": 1,
        "naechste_pruefung_am": "2027-04-01",
        "last_psa_pruefung_am": None,
        "last_psa_pruefung_kommentar": None,
        "last_psa_durchgefallen_am": None,
    }


def test_database_accepts_duplicate_and_empty_ei(legacy: Legacy) -> None:
    """Zeile 1: Die Eindeutigkeit prüft nur die Oberfläche, die Datenbank nimmt alles an."""
    legacy.product("TST-0101")
    legacy.product("TST-0101")
    legacy.product("")

    assert len(legacy.rows("SELECT id FROM products")) == 3


@pytest.mark.parametrize(
    ("candidate", "conflict"),
    [
        ("TST-0200", "bereits bei Produkt #1 vorhanden"),
        ("tst-0200", "bereits bei Produkt #1 vorhanden"),
        ("  TST-0200 ", "bereits bei Produkt #1 vorhanden"),
        ("TST 0200", None),  # Leerzeichen innen: im Altsystem eine andere EI
        ("TST-020", None),
        ("", None),
        ("   ", None),
        ("TST-0201", "bereits bei System #1, Teil 2 vorhanden"),
        ("tst-0201", "bereits bei System #1, Teil 2 vorhanden"),
    ],
)
def test_ei_conflict_ignores_case_and_outer_spaces_only(
    candidate: str, conflict: str | None, legacy: Legacy
) -> None:
    """Zeile 1: `LOWER(TRIM(...))` – Groß-/Kleinschreibung und Rand-Leerzeichen, nicht innen."""
    legacy.product("TST-0200")
    legacy.system("Gurt-Set", "TST-0202", "TST-0201")

    assert legacy.psa.find_einzelidentifikation_conflict(legacy.db, candidate) == conflict


def test_ei_conflict_finds_stored_ei_with_outer_spaces(legacy: Legacy) -> None:
    legacy.product(" TST-0300 ")

    assert legacy.psa.find_einzelidentifikation_conflict(legacy.db, "TST-0300") is not None


def test_system_always_has_three_part_rows(legacy: Legacy) -> None:
    """Zeile 2: Systeme haben fest drei Teile; leere Teile werden trotzdem gespeichert."""
    system_id = legacy.system("Gurt-Set", "TST-0400")

    details = legacy.psa.fetch_system_details(legacy.db, system_id)

    assert sorted(details["parts"]) == [1, 2, 3]
    assert details["parts"][1]["einzelidentifikation"] == "TST-0400"
    assert details["parts"][2]["einzelidentifikation"] == ""
    assert details["system"]["name"] == "Gurt-Set"


def test_update_system_adds_missing_part_rows(legacy: Legacy) -> None:
    system_id = int(legacy.psa.insert_system(legacy.db, "Altes Set"))
    legacy.psa.insert_system_part(legacy.db, system_id, 1, {"einzelidentifikation": "TST-0500"})
    details = legacy.psa.fetch_system_details(legacy.db, system_id)

    legacy.psa.update_system(legacy.db, system_id, details["system"], details["parts"])

    parts = legacy.rows(
        "SELECT part_index, einzelidentifikation FROM system_parts WHERE system_id = ? "
        "ORDER BY part_index",
        (system_id,),
    )
    assert parts == [
        {"part_index": 1, "einzelidentifikation": "TST-0500"},
        {"part_index": 2, "einzelidentifikation": ""},
        {"part_index": 3, "einzelidentifikation": ""},
    ]


def test_update_product_overwrites_every_field(legacy: Legacy) -> None:
    """Zeile 3: Bearbeiten schreibt alle Felder; fehlende Schlüssel werden zu leerem Text.

    Auch „nächste Prüfung“ ist im Altsystem ein frei bearbeitbares Feld.
    """
    product_id = legacy.product("TST-0600", naechste_pruefung_am="2027-01-01")

    legacy.psa.update_product(
        legacy.db,
        product_id,
        {"einzelidentifikation": "TST-0600", "naechste_pruefung_am": "2030-01-01"},
    )

    details = legacy.psa.fetch_product_details(legacy.db, product_id)
    assert details["naechste_pruefung_am"] == "2030-01-01"
    assert details["hersteller"] == ""
    assert details["keine_psa"] == 0
    assert details["last_psa_pruefung_am"] == ""


@pytest.mark.parametrize(
    ("start", "months", "expected"),
    [
        ("2026-10-15", 6, "2027-04-15"),
        ("2026-10-15", 12, "2027-10-15"),
        ("2026-08-31", 6, "2027-02-28"),
        ("2028-02-29", 12, "2029-02-28"),
        ("2026-12-31", 1, "2027-01-31"),
    ],
)
def test_quick_pick_adds_months_and_clamps_to_month_end(
    start: str, months: int, expected: str, legacy: Legacy
) -> None:
    """Zeile 3: Schnellwahl +6M/+1J."""
    assert legacy.psa._add_months_iso(start, months) == expected


def test_relabel_updates_loan_labels(legacy: Legacy) -> None:
    """Zeile 3/4: Nach dem Bearbeiten übernimmt der Verleih die neue Beschriftung."""
    product_id = legacy.product("TST-0700")
    legacy.loan(product_id, 1, 2, status="reserved")
    details = legacy.psa.fetch_product_details(legacy.db, product_id)
    legacy.psa.update_product(legacy.db, product_id, {**details, "produktname": "Neu"})

    legacy.psa.refresh_verleih_item_label(legacy.db, item_type="product", item_id=product_id)

    label = legacy.rows("SELECT item_label FROM verleih_planung")[0]["item_label"]
    assert label == "EI: TST-0700 | PB: Klettergurt | Name: Neu | SN: SN-TEST-1"


def test_gal_link_can_be_set_on_several_items(legacy: Legacy) -> None:
    """Zeile 4: GAL für mehrere Teile – im Altsystem je Teil ein eigener Eintrag (und Datei)."""
    first, second = legacy.product("TST-0800"), legacy.product("TST-0801")
    for product_id in (first, second):
        details = legacy.psa.fetch_product_details(legacy.db, product_id)
        legacy.psa.update_product(
            legacy.db, product_id, {**details, "gal_link": "https://example.org/gal.pdf"}
        )

    assert {
        legacy.verleih.fetch_item_gal_data(legacy.db, item_type="product", item_id=item)
        for item in (first, second)
    } == {("", "https://example.org/gal.pdf")}


def test_product_list_marks_locked_items(legacy: Legacy) -> None:
    first, second = legacy.product("TST-0901"), legacy.product("tst-0900")
    legacy.psa.lock_item(legacy.db, item_type="product", item_id=first, lock_comment="Riss")

    assert legacy.psa.fetch_products(legacy.db) == [
        (second, "EI: tst-0900 | PB: Klettergurt | Name: Testmodell A1 | SN: SN-TEST-1"),
        (
            first,
            "EI: TST-0901 | PB: Klettergurt | Name: Testmodell A1 | SN: SN-TEST-1 | [GESPERRT]",
        ),
    ]
