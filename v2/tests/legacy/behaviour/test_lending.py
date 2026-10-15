"""Altsystem, Verleih (Paritätszeilen 12 bis 20): so verhält sich `verleih_gui.py` heute.

Nur in der Oberfläche stehen: „Rückgabe vor der nächsten Prüfung“ (`verleih_gui.py`, beim
Ausleihen und beim Umwandeln einer Reservierung), die Pflichtangaben der Ausgabe und der Hinweis
„überfällig“ (`_refresh_plan_list`). Festgeschrieben sind hier die Datenbankfunktionen.
"""

from datetime import UTC, datetime

import pytest

from tests.legacy.behaviour.conftest import SIGNATURE, Legacy

# --- Zeile 13: Verfügbarkeit ---


@pytest.mark.parametrize(
    ("next_check", "no_ppe", "available"),
    [
        (365, False, True),
        (0, False, True),  # Prüfung heute fällig: noch verfügbar
        (-1, False, False),
        (None, False, False),  # ohne Datum
        (None, True, True),  # „keine PSA“
        (-30, True, True),
    ],
)
def test_availability_needs_current_ppe_or_no_ppe(
    next_check: int | None, no_ppe: bool, available: bool, legacy: Legacy
) -> None:
    due = legacy.day(next_check) if next_check is not None else ""
    product_id = legacy.product(naechste_pruefung_am=due, keine_psa=no_ppe)

    assert legacy.available(product_id, 1, 2) is available


def test_today_is_the_utc_date(legacy: Legacy) -> None:
    """Zeile 13: „heute“ ist `DATE('now')` und damit das Datum in UTC, nicht in deutscher Zeit."""
    assert legacy.today() == datetime.now(UTC).date()


def test_return_after_next_check_is_not_checked_by_database(legacy: Legacy) -> None:
    """Zeile 13: „Rückgabe vor nächster Prüfung“ prüft nur die Oberfläche."""
    product_id = legacy.product(naechste_pruefung_am=legacy.day(5))

    assert legacy.available(product_id, 1, 30) is True


@pytest.mark.parametrize(
    ("next_check", "available"),
    [
        ("31.12.2000", True),
        ("21.01.2000", True),
        ("20.12.2099", False),
        ("01.01.2099", False),
    ],
)
def test_german_dates_compare_as_text(next_check: str, available: bool, legacy: Legacy) -> None:
    """Zeile 13: `naechste_pruefung_am >= DATE('now')` vergleicht Text.

    `TT.MM.JJJJ` gilt deshalb ab Tag 21 immer als aktuell, bis Tag 20 nie, unabhängig vom Jahr.
    """
    product_id = legacy.product(naechste_pruefung_am=next_check)

    assert legacy.available(product_id, 1, 2) is available


def test_locked_item_is_not_available(legacy: Legacy) -> None:
    product_id = legacy.product()
    legacy.psa.lock_item(legacy.db, item_type="product", item_id=product_id, lock_comment="Riss")

    assert legacy.available(product_id, 1, 2) is False

    legacy.psa.unlock_item(legacy.db, item_type="product", item_id=product_id, unlock_comment="ok")
    assert legacy.available(product_id, 1, 2) is True


@pytest.mark.parametrize(
    ("start", "end", "available"),
    [
        (3, 7, False),
        (7, 9, False),  # Übergabe am Rückgabetag zählt als Überschneidung
        (0, 3, False),
        (8, 9, True),
        (0, 2, True),
        (5, 5, False),
    ],
)
def test_overlap_includes_both_end_days(
    start: int, end: int, available: bool, legacy: Legacy
) -> None:
    product_id = legacy.product()
    legacy.loan(product_id, 3, 7, status="reserved")

    assert legacy.available(product_id, start, end) is available


@pytest.mark.parametrize(
    ("status", "blocks"), [("reserved", True), ("lent", True), ("returned", False), ("", False)]
)
def test_only_reserved_and_lent_block(status: str, blocks: bool, legacy: Legacy) -> None:
    """Zeile 13: Ein leerer Status blockiert nicht (`COALESCE` greift nur bei NULL)."""
    product_id = legacy.product()
    legacy.loan(product_id, 1, 3, status=status)

    assert legacy.available(product_id, 2, 2) is not blocks


def test_overdue_loan_blocks_only_its_own_period(legacy: Legacy) -> None:
    """Zeile 13/18: Ein überfälliges, nicht zurückgegebenes Teil gilt danach als frei."""
    product_id = legacy.product()
    legacy.loan(product_id, -10, -2, status="lent")

    assert legacy.available(product_id, 1, 2) is True


def test_database_accepts_overlapping_loans(legacy: Legacy) -> None:
    """Zeile 13: Doppelbuchungen verhindert nur die Prüfung vor dem Speichern."""
    product_id = legacy.product()
    legacy.loan(product_id, 1, 5, status="reserved")
    legacy.loan(product_id, 2, 3, status="lent")

    assert len(legacy.rows("SELECT id FROM verleih_planung")) == 2


def test_systems_follow_the_same_rules(legacy: Legacy) -> None:
    system_id = legacy.system("Set", "TST-S1", naechste_pruefung_am=legacy.day(-1))
    assert legacy.available(system_id, 1, 2, item_type="system") is False

    legacy.execute("UPDATE systems SET keine_psa = 1")
    assert legacy.available(system_id, 1, 2, item_type="system") is True


# --- Zeilen 12 und 20: Auswahl und Suche ---


def test_search_finds_products_by_designation_name_or_serial(legacy: Legacy) -> None:
    """Zeile 20: Verfügbarkeitssuche nach Bezeichnung, Name, Seriennummer und Zeitraum."""
    rope = legacy.product(
        "TST-R", produktbezeichnung="Seil", produktname="Dynamo", seriennummer="X9"
    )
    legacy.product("TST-G")

    for text in ("Seil", "Dyna", "X9", "seil"):
        found = legacy.verleih.find_available_items(legacy.db, text, legacy.day(1), legacy.day(2))
        assert [row["item_id"] for row in found] == [rope], text


def test_search_finds_systems_by_name_only(legacy: Legacy) -> None:
    system_id = legacy.system("Klettersteig-Set", "TST-KS")

    by_name = legacy.verleih.find_available_items(legacy.db, "steig", legacy.day(1), legacy.day(2))
    by_ei = legacy.verleih.find_available_items(legacy.db, "TST-KS", legacy.day(1), legacy.day(2))

    assert [(r["item_type"], r["item_id"], r["item_label"]) for r in by_name] == [
        ("system", system_id, "EI: TST-KS | Name: Klettersteig-Set")
    ]
    assert by_ei == []


def test_search_applies_availability_rules(legacy: Legacy) -> None:
    free = legacy.product("TST-1")
    expired = legacy.product("TST-2", naechste_pruefung_am=legacy.day(-1))
    locked = legacy.product("TST-3")
    booked = legacy.product("TST-4")
    legacy.psa.lock_item(legacy.db, item_type="product", item_id=locked, lock_comment="Riss")
    legacy.loan(booked, 1, 1, status="reserved")

    found = legacy.verleih.find_available_items(legacy.db, "", legacy.day(1), legacy.day(2))

    assert [row["item_id"] for row in found] == [free]
    assert expired not in [row["item_id"] for row in found]


def test_designation_filter_lists_only_lendable_designations(legacy: Legacy) -> None:
    """Zeile 12: Filter nach Bezeichnung – nur Bezeichnungen mit aktueller PSA."""
    legacy.product("TST-1", produktbezeichnung=" Seil ")
    legacy.product("TST-2", produktbezeichnung="Helm", naechste_pruefung_am=legacy.day(-1))
    legacy.product("TST-3", produktbezeichnung="Karabiner", keine_psa=True, naechste_pruefung_am="")
    legacy.system("Klettersteig-Set", "TST-4")

    assert legacy.verleih.fetch_distinct_produktbezeichnungen(legacy.db) == [
        "Karabiner",
        "Klettersteig-Set",
        "Seil",
    ]


# --- Zeilen 14, 15, 17, 19: Ausgabe, Umwandeln, Rückgabe, Details ---


def test_loan_stores_all_checkout_fields(legacy: Legacy) -> None:
    """Zeile 14 und 19: Entleiher mit Kontaktdaten, ausgebende Person, Kontrollen, Unterschrift."""
    product_id = legacy.product()
    loan_id = legacy.loan(product_id, 0, 2, status="lent")

    loan = legacy.verleih.fetch_verleih_by_id(legacy.db, loan_id)

    assert {key: loan[key] for key in ("entleiher", "ausgebende_person", "status")} == {
        "entleiher": "Kim Beispiel",
        "ausgebende_person": "Testperson Muster",
        "status": "lent",
    }
    assert (loan["quick_check_out"], loan["gal_provided_out"]) == (1, 1)
    assert loan["signature_data"] == SIGNATURE
    assert loan["created_at"]


def test_database_accepts_checkout_without_confirmations(legacy: Legacy) -> None:
    """Zeile 14: Kurzkontrolle, GAL und Unterschrift sind nur in der Oberfläche Pflicht."""
    loan_id = legacy.loan(
        legacy.product(), 0, 1, status="lent", quick_check_out=False, signature_data=""
    )

    assert legacy.verleih.fetch_verleih_by_id(legacy.db, loan_id)["quick_check_out"] == 0


def test_reservation_is_converted_once(legacy: Legacy) -> None:
    """Zeile 15: Reservierung in Ausleihe umwandeln; nur aus „reserved“ heraus."""
    product_id = legacy.product()
    loan_id = legacy.loan(product_id, 0, 2, status="reserved")

    legacy.verleih.checkout_reserved_verleih(
        legacy.db, loan_id, SIGNATURE, True, True, "Testperson Muster"
    )
    loan = legacy.verleih.fetch_verleih_by_id(legacy.db, loan_id)
    assert (loan["status"], loan["ausgebende_person"], loan["quick_check_out"]) == (
        "lent",
        "Testperson Muster",
        1,
    )
    first_checkout = loan["checkout_at"]

    legacy.verleih.checkout_reserved_verleih(legacy.db, loan_id, "", False, False, "Andere Person")
    loan = legacy.verleih.fetch_verleih_by_id(legacy.db, loan_id)
    assert (loan["ausgebende_person"], loan["checkout_at"]) == ("Testperson Muster", first_checkout)


def test_return_only_from_lent(legacy: Legacy) -> None:
    """Zeile 17: Rückgabe mit Kommentar, Kurzprüfung, rücknehmender Person und Unterschrift."""
    reserved = legacy.loan(legacy.product("TST-1"), 1, 2, status="reserved")
    lent = legacy.loan(legacy.product("TST-2"), 0, 2, status="lent")

    for loan_id in (reserved, lent):
        legacy.verleih.confirm_return(
            legacy.db,
            loan_id,
            return_comment="alles gut",
            ruecknehmende_person="Testperson Muster",
            return_signature_data=SIGNATURE,
            quick_check_return=True,
        )

    assert legacy.verleih.fetch_verleih_by_id(legacy.db, reserved)["status"] == "reserved"
    returned = legacy.verleih.fetch_verleih_by_id(legacy.db, lent)
    assert (returned["status"], returned["return_comment"], returned["quick_check_return"]) == (
        "returned",
        "alles gut",
        1,
    )
    assert returned["return_signature_data"] == SIGNATURE  # Zeile 19: beide Unterschriften
    assert returned["signature_data"] == SIGNATURE
    assert returned["returned_at"]


def test_lock_on_return_overwrites_previous_lock(legacy: Legacy) -> None:
    """Zeile 17: optional sperren – dieselbe Sperrtabelle wie im PSA-Programm."""
    product_id = legacy.product()
    legacy.psa.lock_item(legacy.db, item_type="product", item_id=product_id, lock_comment="alt")
    legacy.psa.unlock_item(legacy.db, item_type="product", item_id=product_id, unlock_comment="ok")

    legacy.verleih.lock_item(
        legacy.db, item_type="product", item_id=product_id, lock_comment="Naht offen"
    )

    assert legacy.verleih.is_item_locked(legacy.db, item_type="product", item_id=product_id)
    lock = legacy.rows("SELECT * FROM item_locks")[0]
    assert (lock["lock_comment"], lock["unlock_comment"]) == ("Naht offen", None)


# --- Zeile 16: Kontaktdaten ---


@pytest.mark.parametrize(
    ("status", "changed"), [("reserved", True), ("lent", True), ("returned", False)]
)
def test_contact_can_be_added_while_active(status: str, changed: bool, legacy: Legacy) -> None:
    loan_id = legacy.loan(legacy.product(), 0, 1, status=status)

    legacy.verleih.update_verleih_contact(
        legacy.db,
        loan_id,
        entleiher="Robin Exempel",
        entleiher_email="",
        entleiher_telefon="0000 654321",
        entleiher_adresse="Beispielweg 4",
    )

    name = legacy.verleih.fetch_verleih_by_id(legacy.db, loan_id)["entleiher"]
    assert (name == "Robin Exempel") is changed


# --- Zeilen 18 und 20: Übersicht und Kalender ---


def test_pending_list_hides_past_reservations_but_keeps_overdue_loans(legacy: Legacy) -> None:
    """Zeile 18: Übersicht anstehender Ausleihen."""
    product_id = legacy.product()
    legacy.loan(product_id, -10, -8, status="reserved")  # verfallene Reservierung
    overdue = legacy.loan(product_id, -7, -5, status="lent")
    upcoming = legacy.loan(product_id, 2, 3, status="reserved")
    today = legacy.loan(product_id, 0, 1, status="reserved")
    legacy.loan(product_id, 4, 5, status="returned")

    pending = legacy.verleih.fetch_pending_verleihplaene(legacy.db)

    assert [row["id"] for row in pending] == [overdue, today, upcoming]


def test_calendar_lists_active_loans_touching_the_range(legacy: Legacy) -> None:
    """Zeile 20: Monatskalender mit Tagesliste."""
    product_id = legacy.product()
    inside = legacy.loan(product_id, 3, 5, status="reserved")
    legacy.loan(product_id, 10, 12, status="reserved")
    legacy.loan(product_id, 4, 4, status="returned")
    touching = legacy.loan(product_id, -2, 1, status="lent")

    entries = legacy.verleih.fetch_calendar_entries(
        legacy.db, from_date=legacy.day(1), to_date=legacy.day(5)
    )

    assert [row["id"] for row in entries] == [touching, inside]


# --- Start des Verleih-Programms ---


def test_startup_fills_empty_status_by_signature(legacy: Legacy) -> None:
    """Beim Start wird ein leerer Status gesetzt: mit Unterschrift „lent“, sonst „reserved“."""
    product_id = legacy.product()
    signed = legacy.loan(product_id, 0, 1, status="", signature_data=SIGNATURE)
    unsigned = legacy.loan(product_id, 3, 4, status="", signature_data="")

    legacy.verleih.ensure_verleih_schema(legacy.db)

    statuses = {
        row["id"]: row["status"] for row in legacy.rows("SELECT id, status FROM verleih_planung")
    }
    assert statuses == {signed: "lent", unsigned: "reserved"}
