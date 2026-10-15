"""Altsystem, PSA (Paritätszeilen 6 bis 10): so verhält sich `psa_gui.py` heute.

Den Ablauf einer Prüfungsrunde (sammeln, dann alles auf einmal anwenden) steuert die Oberfläche
(`commit_pending_pruefungen`); festgeschrieben sind die Datenbankfunktionen, die sie aufruft.
"""

from tests.legacy.behaviour.conftest import SIGNATURE, Legacy


def _due(legacy: Legacy) -> list[tuple[str, int, str, int]]:
    return [
        (row["item_type"], row["item_id"], row["naechste_pruefung_am"], row["is_locked"])
        for row in legacy.psa.fetch_due_items(legacy.db)
    ]


def test_due_list_puts_locked_first_then_by_date_and_empty_last(legacy: Legacy) -> None:
    """Zeile 6: Liste „Nächste Prüfungen“, gesperrte Teile oben."""
    later = legacy.product("TST-A", naechste_pruefung_am="2027-06-01")
    sooner = legacy.product("TST-B", naechste_pruefung_am="2027-01-01")
    empty = legacy.product("TST-C", naechste_pruefung_am="")
    locked = legacy.product("TST-D", naechste_pruefung_am="2028-01-01")
    system = legacy.system("Set", "TST-E", naechste_pruefung_am="2027-03-01")
    legacy.psa.lock_item(legacy.db, item_type="product", item_id=locked, lock_comment="Riss")

    assert _due(legacy) == [
        ("product", locked, "2028-01-01", 1),
        ("product", sooner, "2027-01-01", 0),
        ("system", system, "2027-03-01", 0),
        ("product", later, "2027-06-01", 0),
        ("product", empty, "", 0),
    ]


def test_due_list_sorts_dates_as_text(legacy: Legacy) -> None:
    """Zeile 6: Datum wird als Text sortiert; `TT.MM.JJJJ` landet daher an falscher Stelle."""
    german = legacy.product("TST-A", naechste_pruefung_am="01.01.2027")
    iso = legacy.product("TST-B", naechste_pruefung_am="2027-06-01")

    assert [item_id for _, item_id, _, _ in _due(legacy)] == [german, iso]


def test_due_list_labels(legacy: Legacy) -> None:
    legacy.product("TST-A")
    legacy.system("Set", "", "TST-S2")  # erstes Teil ohne EI

    labels = {row["item_label"] for row in legacy.psa.fetch_due_items(legacy.db)}

    assert labels == {
        "EI: TST-A | PB: Klettergurt | Name: Testmodell A1 | SN: SN-TEST-1",
        "EI:  | Name: Set",  # leere EI ist '' und nicht NULL, daher kein „-“
    }


def test_lock_and_unlock_keep_only_the_latest_state(legacy: Legacy) -> None:
    """Zeile 7: Sperren/Entsperren mit Kommentar; eine neue Sperre löscht die alte Entsperrung."""
    product_id = legacy.product()

    assert (
        legacy.psa.unlock_item(
            legacy.db, item_type="product", item_id=product_id, unlock_comment="grundlos"
        )
        is False
    )
    legacy.psa.lock_item(legacy.db, item_type="product", item_id=product_id, lock_comment="Riss")
    assert (
        legacy.psa.unlock_item(
            legacy.db, item_type="product", item_id=product_id, unlock_comment="repariert"
        )
        is True
    )
    legacy.psa.lock_item(legacy.db, item_type="product", item_id=product_id, lock_comment="Sturz")

    locks = legacy.rows("SELECT * FROM item_locks")
    assert len(locks) == 1
    lock = locks[0]
    assert (lock["is_locked"], lock["lock_comment"], lock["unlock_comment"]) == (1, "Sturz", None)
    assert lock["unlock_at"] is None and lock["unlock_source"] is None


def test_unlock_records_source(legacy: Legacy) -> None:
    product_id = legacy.product()
    legacy.psa.lock_item(legacy.db, item_type="product", item_id=product_id, lock_comment="Riss")

    legacy.psa.unlock_item(legacy.db, item_type="product", item_id=product_id, unlock_comment="ok")

    lock = legacy.rows("SELECT * FROM item_locks")[0]
    assert (lock["is_locked"], lock["unlock_comment"], lock["unlock_source"]) == (
        0,
        "ok",
        "psa_gui",
    )
    assert lock["lock_comment"] == "Riss"


def test_lock_accepts_empty_comment(legacy: Legacy) -> None:
    """Zeile 7: Den Pflichtkommentar prüft nur die Oberfläche."""
    product_id = legacy.product()

    assert legacy.psa.lock_item(legacy.db, item_type="product", item_id=product_id, lock_comment="")


def test_report_stores_signer_confirmation_signature_and_items(legacy: Legacy) -> None:
    """Zeile 8 und 9: Abschluss mit Name, „Ich bin PSA-Sachkundig“ und Unterschrift."""
    product_id = legacy.product()

    report_id = legacy.psa.create_psa_report(
        legacy.db,
        signer_name="Testperson Prüfer",
        signature_data=SIGNATURE,
        items=[
            {
                "item_type": "product",
                "item_id": product_id,
                "item_label": "EI: TST-0001",
                "psa_done": True,
                "kommentar": "ohne Befund",
                "naechste_pruefung_am": "2027-10-01",
                "aktion": "unlock+update",
            },
            {"item_type": "system", "item_id": 99, "item_label": "Set"},
        ],
    )

    details = legacy.psa.fetch_report_details(legacy.db, report_id)
    header = details["header"]
    assert (header["sachkundiger_name"], header["sachkundig_bestaetigt"]) == (
        "Testperson Prüfer",
        1,
    )
    assert header["signature_data"] == SIGNATURE
    first, second = details["items"]
    assert (first["psa_pruefung_durchgefuehrt"], first["aktion"], first["kommentar"]) == (
        1,
        "unlock+update",
        "ohne Befund",
    )
    assert (second["psa_pruefung_durchgefuehrt"], second["aktion"], second["kommentar"]) == (
        0,
        "update",
        "",
    )


def test_report_list_newest_first_with_item_count(legacy: Legacy) -> None:
    """Zeile 9: Liste der Prüfberichte."""
    first = legacy.psa.create_psa_report(
        legacy.db, signer_name="A", signature_data=SIGNATURE, items=[{"item_label": "x"}]
    )
    second = legacy.psa.create_psa_report(
        legacy.db, signer_name="B", signature_data=SIGNATURE, items=[]
    )

    reports = legacy.psa.fetch_reports(legacy.db)

    assert [(r["id"], r["item_count"]) for r in reports] == [(second, 0), (first, 1)]
    assert legacy.psa.fetch_report_details(legacy.db, 999) is None


def test_item_history_lists_locks_reports_and_failures_newest_first(legacy: Legacy) -> None:
    """Zeile 6: Vorgänge je Teil."""
    product_id = legacy.product()
    legacy.execute(
        "INSERT INTO item_locks (item_type, item_id, is_locked, lock_comment, lock_at, "
        "unlock_comment, unlock_at) VALUES ('product', ?, 0, 'Riss', '2026-02-01T09:00:00', "
        "'repariert', '2026-03-01T09:00:00')",
        (product_id,),
    )
    legacy.execute(
        "INSERT INTO psa_pruefungsberichte (created_at, sachkundiger_name, signature_data) "
        "VALUES ('2026-04-01T10:00:00', 'A', '')"
    )
    legacy.execute(
        "INSERT INTO psa_pruefungsbericht_items (report_id, item_type, item_id, item_label, "
        "kommentar, naechste_pruefung_am, aktion) VALUES (1, 'product', ?, 'x', 'gut', "
        "'2027-04-01', 'update')",
        (product_id,),
    )
    legacy.execute("UPDATE products SET last_psa_durchgefallen_am = '2026-01-15T08:00:00'")

    assert legacy.psa.fetch_item_vorgaenge(legacy.db, item_type="product", item_id=product_id) == [
        "2026-04-01 10:00:00 | PSA-Prüfung | Aktion: update | Nächste Prüfung: 2027-04-01 | "
        "Kommentar: gut",
        "2026-03-01 09:00:00 | Entsperrt | Kommentar: repariert",
        "2026-02-01 09:00:00 | Gesperrt | Kommentar: Riss",
        "2026-01-15 08:00:00 | PSA-Prüfung: Durchgefallen",
    ]


def test_archive_copies_item_with_history_and_delete_keeps_loans(legacy: Legacy) -> None:
    """Zeile 10: Archiv ausgesonderter Teile mit Historie, als zweite Datenbankdatei."""
    product_id = legacy.product("TST-ARCH")
    legacy.loan(product_id, -20, -10, status="returned")
    legacy.psa.lock_item(legacy.db, item_type="product", item_id=product_id, lock_comment="Riss")
    legacy.psa.create_psa_report(
        legacy.db,
        signer_name="A",
        signature_data=SIGNATURE,
        items=[{"item_type": "product", "item_id": product_id, "item_label": "x"}],
    )

    archived = legacy.psa.archive_item_with_history(
        legacy.db,
        item_type="product",
        item_id=product_id,
        item_label="EI: TST-ARCH",
        kommentar="Ablegereife",
        archived_by="Testperson Prüfer",
        archive_db_path=legacy.archive,
    )
    legacy.psa.delete_item(legacy.db, item_type="product", item_id=product_id)

    assert archived is True
    for table in ("products", "verleih_planung", "item_locks", "psa_pruefungsberichte"):
        assert len(legacy.rows(f"SELECT * FROM {table}", archive=True)) == 1, table  # noqa: S608
    entry = legacy.psa.fetch_archived_items(legacy.archive)[0]
    assert (entry["source_item_id"], entry["kommentar"], entry["archived_by"]) == (
        product_id,
        "Ablegereife",
        "Testperson Prüfer",
    )
    # In der Hauptdatenbank verschwinden Teil und Sperre, Ausleihen und Berichte bleiben.
    assert legacy.rows("SELECT * FROM products") == []
    assert legacy.rows("SELECT * FROM item_locks") == []
    assert len(legacy.rows("SELECT * FROM verleih_planung")) == 1
    assert len(legacy.rows("SELECT * FROM psa_pruefungsbericht_items")) == 1


def test_archive_of_system_copies_its_parts(legacy: Legacy) -> None:
    system_id = legacy.system("Set", "TST-S1", "TST-S2")

    legacy.psa.archive_item_with_history(
        legacy.db,
        item_type="system",
        item_id=system_id,
        item_label="Set",
        kommentar="",
        archived_by="",
        archive_db_path=legacy.archive,
    )
    legacy.psa.delete_item(legacy.db, item_type="system", item_id=system_id)

    assert len(legacy.rows("SELECT * FROM system_parts", archive=True)) == 3
    assert legacy.rows("SELECT * FROM system_parts") == []


def test_archive_of_missing_item_fails(legacy: Legacy) -> None:
    assert (
        legacy.psa.archive_item_with_history(
            legacy.db,
            item_type="product",
            item_id=42,
            item_label="x",
            kommentar="",
            archived_by="",
            archive_db_path=legacy.archive,
        )
        is False
    )


def test_ids_are_not_reused_after_delete(legacy: Legacy) -> None:
    """Zeile 10: AUTOINCREMENT – Ausleihen archivierter Teile zeigen nie auf ein neues Teil."""
    first = legacy.product("TST-1")
    legacy.psa.delete_item(legacy.db, item_type="product", item_id=first)

    assert legacy.product("TST-2") == first + 1


def test_active_loans_block_deletion_check(legacy: Legacy) -> None:
    """Zeile 7: „löschen = archivieren“ ist nur ohne aktive Reservierung/Ausleihe erlaubt."""
    product_id = legacy.product()
    legacy.loan(product_id, -5, -1, status="returned")
    assert legacy.psa.has_active_loans(legacy.db, item_type="product", item_id=product_id) is False

    legacy.loan(product_id, 3, 4, status="reserved")
    assert legacy.psa.has_active_loans(legacy.db, item_type="product", item_id=product_id) is True
