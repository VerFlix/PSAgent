"""Importwerkzeug mit Probelauf (#10): alle Schema-Varianten, nur künstliche Daten."""

import sqlite3
from contextlib import closing
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pytest
from django.apps import apps
from django.utils import timezone

from inspections.services import next_inspection_due
from inventory.models import Component, Document, EventKind, Part, PartStatus
from legacy.importer import LegacyImportError, run_import
from legacy.models import LegacyReport, LegacyReportItem
from legacy.schema import VARIANTS
from lending.models import Booking, Borrower, Loan, LoanEvent, LoanEventKind, Signature
from tests.legacy.conftest import Generate, fingerprint

pytestmark = pytest.mark.django_db

APPS = ("inventory", "inspections", "lending", "legacy")


def row_counts() -> dict[str, int]:
    return {
        model._meta.label: model._default_manager.count()
        for app in APPS
        for model in apps.get_app_config(app).get_models()
    }


def rows(db: Path | None, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    assert db is not None
    with closing(sqlite3.connect(db)) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute(sql, params)]


def execute(db: Path | None, sql: str, params: tuple[Any, ...] = ()) -> None:
    assert db is not None
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute(sql, params)


def part(kind: str, legacy_id: int) -> Part:
    return Part.objects.get(legacy_kind=kind, legacy_id=legacy_id)


# --- Alle Varianten ---


@pytest.mark.parametrize("variant", list(VARIANTS))
def test_dry_run_changes_nothing(
    variant: str, generated_sources: Generate, media_root: Path
) -> None:
    """Abnahme #10: Der Probelauf verändert die Datenbank nachweislich nicht."""
    source = generated_sources(variant)
    before_sources = fingerprint(source.main_db.parent)
    before_rows = row_counts()

    report = run_import(source.main_db.parent, dry_run=True)

    assert row_counts() == before_rows
    assert not media_root.exists() or fingerprint(media_root) == {}
    assert fingerprint(source.main_db.parent) == before_sources
    assert report.dry_run is True
    assert sum(c.imported for c in report.counts.values()) > 0


@pytest.mark.parametrize("variant", list(VARIANTS))
def test_import_accounts_for_every_row(
    variant: str, generated_sources: Generate, media_root: Path
) -> None:
    """Abnahme #10: alle Varianten; jede gelesene Zeile ist übernommen, abgelehnt oder leer."""
    source = generated_sources(variant)

    report = run_import(source.main_db.parent, dry_run=False)

    for table, counts in report.counts.items():
        assert counts.read == counts.imported + counts.rejected + counts.skipped, table
    imported = report.counts
    assert Part.objects.count() == (
        imported["products"].imported
        + imported["systems"].imported
        + imported["archived_items"].imported
    )
    assert Component.objects.filter(part__kind="system").count() == (
        imported["system_parts"].imported
    )
    assert Loan.objects.count() == imported["verleih_planung"].imported
    assert LegacyReport.objects.count() == imported["psa_pruefungsberichte"].imported
    assert LegacyReportItem.objects.count() == imported["psa_pruefungsbericht_items"].imported
    assert Part.objects.filter(events__kind=EventKind.RETIRED).count() == (
        imported["archived_items"].imported
    )


@pytest.mark.parametrize("variant", ["s7", "s5_aktualisiert"])
def test_dry_run_and_import_give_the_same_report(
    variant: str, generated_sources: Generate, media_root: Path
) -> None:
    source = generated_sources(variant).main_db.parent

    dry = run_import(source, dry_run=True)
    real = run_import(source, dry_run=False)

    assert (dry.counts, dry.rejected, dry.warnings, dry.clarifications) == (
        real.counts,
        real.rejected,
        real.warnings,
        real.clarifications,
    )
    assert dry.sources == real.sources


def test_clean_data_imports_without_rejections(
    generated_sources: Generate, media_root: Path
) -> None:
    source = generated_sources("s7", edge_cases=False)

    report = run_import(source.main_db.parent, dry_run=False)

    assert report.rejected == []
    assert report.warnings == []


# --- Ziel und Quelle ---


def test_refuses_non_empty_target(generated_sources: Generate, media_root: Path) -> None:
    source = generated_sources().main_db.parent
    run_import(source, dry_run=False)
    before = row_counts()

    with pytest.raises(LegacyImportError, match="bereits Daten"):
        run_import(source, dry_run=True)

    assert row_counts() == before


def test_refuses_missing_source(tmp_path: Path) -> None:
    with pytest.raises(LegacyImportError, match=r"psa\.db"):
        run_import(tmp_path, dry_run=True)


def test_sources_are_read_only(generated_sources: Generate, media_root: Path) -> None:
    source = generated_sources("s6_aktualisiert")
    before = fingerprint(source.main_db.parent)

    run_import(source.main_db.parent, dry_run=False)

    assert fingerprint(source.main_db.parent) == before


def test_report_lists_source_hashes(generated_sources: Generate) -> None:
    report = run_import(generated_sources().main_db.parent, dry_run=True)

    assert [name for name, _ in report.sources] == ["psa.db", "psa_archiv.db", "GAL/"]
    assert all(len(digest) == 64 for _, digest in report.sources)


# --- Keine Personendaten im Bericht ---


def test_report_contains_no_personal_data(generated_sources: Generate) -> None:
    """Abnahme #10: Der Bericht verweist per EI bzw. Nummer, ohne Personendaten."""
    source = generated_sources("s7")
    personal: set[str] = set()
    for db in (source.main_db, source.archive_db):
        for row in rows(db, "SELECT * FROM verleih_planung"):
            for column in (
                "entleiher",
                "entleiher_email",
                "entleiher_telefon",
                "entleiher_adresse",
                "ausgebende_person",
                "ruecknehmende_person",
                "signature_data",
                "return_signature_data",
            ):
                personal.add(str(row.get(column) or ""))
        for row in rows(db, "SELECT * FROM psa_pruefungsberichte"):
            personal |= {str(row["sachkundiger_name"]), str(row["signature_data"])}
    for row in rows(source.archive_db, "SELECT * FROM archived_items"):
        personal.add(str(row["archived_by"] or ""))
    personal = {value for value in personal if value.strip()}
    assert len(personal) > 20

    text = run_import(source.main_db.parent, dry_run=True).render()

    assert [value for value in personal if value in text] == []
    assert "@" not in text
    assert "0000 " not in text  # künstliche Telefonnummern beginnen mit 0000


# --- Teile, EI und Prüfstatus ---


def test_ei_collisions_reject_active_parts(generated_sources: Generate, media_root: Path) -> None:
    """Randfälle aus #3: Dubletten nach Normalisierung werden abgelehnt, mit Grund."""
    report = run_import(generated_sources().main_db.parent, dry_run=False)

    for ei in ("TST-0900", "tst-0901", "TST 0902", "TST0902", "TST-0904"):
        assert any(f"EI {ei!r} kollidiert" in line for line in report.rejected), ei
    assert not Component.objects.filter(ei__in=["TST-0900", "TST0902", "TST-0904"]).exists()
    assert any(line.endswith("EI fehlt") for line in report.rejected)
    assert Component.objects.filter(ei="TST-0905").exists() is False  # gibt es nicht
    assert Component.objects.filter(ei="TST-0001").exists()


def test_loans_of_rejected_parts_are_rejected(
    generated_sources: Generate, media_root: Path
) -> None:
    report = run_import(generated_sources().main_db.parent, dry_run=False)

    assert any("wurde nicht übernommen" in line for line in report.rejected)


def test_archived_part_with_colliding_ei_gets_replacement(
    fresh_source: Generate, media_root: Path
) -> None:
    source = fresh_source("s7")
    archived = rows(source.archive_db, "SELECT * FROM archived_items WHERE item_type='product'")
    product_id = archived[0]["source_item_id"]
    execute(
        source.archive_db,
        "UPDATE products SET einzelidentifikation = 'tst-0001' WHERE id = ?",
        (product_id,),
    )

    report = run_import(source.main_db.parent, dry_run=False)

    retired = part("product", product_id)
    assert retired.status() == PartStatus.RETIRED
    assert retired.components.get().ei == f"ALT-P{product_id}-1"
    assert part("product", 1).components.get().ei == "TST-0001"  # das aktive Teil bleibt
    assert any(f"Ersatz-EI ALT-P{product_id}-1" in line for line in report.clarifications)


def test_parts_keep_their_inspection_status(generated_sources: Generate, media_root: Path) -> None:
    """Konzept: Importierte Teile behalten ihren bisherigen Prüfstatus."""
    source = generated_sources()
    run_import(source.main_db.parent, dry_run=False)

    for row in rows(source.main_db, "SELECT * FROM products"):
        try:
            imported = part("product", row["id"])
        except Part.DoesNotExist:
            continue
        raw = (row["naechste_pruefung_am"] or "").strip()
        expected = _legacy_date(raw)
        assert next_inspection_due(imported) == expected, row["id"]
        if row["keine_psa"]:
            assert imported.has_no_ppe()
        assert imported.is_released() is bool(expected or row["keine_psa"]), row["id"]


def _legacy_date(raw: str) -> date | None:
    for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def test_part_without_next_check_stays_new(fresh_source: Generate, media_root: Path) -> None:
    source = fresh_source("s7", edge_cases=False)
    execute(
        source.main_db, "UPDATE products SET naechste_pruefung_am = '', keine_psa = 0 WHERE id = 1"
    )

    report = run_import(source.main_db.parent, dry_run=False)

    assert part("product", 1).is_released() is False
    assert part("product", 1).status() in (PartStatus.NEW, PartStatus.LOCKED)
    assert any("bleibt „neu, nicht freigegeben“" in line for line in report.clarifications)


def test_legacy_inspection_history_goes_into_the_adoption_comment(
    generated_sources: Generate, media_root: Path
) -> None:
    source = generated_sources()
    run_import(source.main_db.parent, dry_run=False)
    row = rows(
        source.main_db,
        "SELECT * FROM products WHERE naechste_pruefung_am LIKE '____-__-__' "
        "AND last_psa_pruefung_am <> '' AND keine_psa = 0 ORDER BY id LIMIT 1",
    )[0]

    result = part("product", row["id"]).inspection_results.get()

    assert result.round.inspector_name == "Altsystem (Übernahme)"
    assert result.round.state().label == "Altbestand übernommen"
    assert f"Letzte Prüfung: {row['last_psa_pruefung_am']}" in result.comment


def test_locks_become_events_with_time_and_comment(
    generated_sources: Generate, media_root: Path
) -> None:
    source = generated_sources()
    run_import(source.main_db.parent, dry_run=False)

    for lock in rows(source.main_db, "SELECT * FROM item_locks"):
        try:
            imported = part(lock["item_type"], lock["item_id"])
        except Part.DoesNotExist:
            continue
        locked = imported.events.get(kind=EventKind.LOCKED)
        assert locked.comment == lock["lock_comment"]
        assert timezone.localtime(locked.at).strftime("%Y-%m-%dT%H:%M:%S") == lock["lock_at"]
        assert imported.is_locked() is (lock["is_locked"] == 1)
        if lock["is_locked"] == 0:
            assert imported.events.get(kind=EventKind.UNLOCKED).comment == lock["unlock_comment"]


def test_archived_items_are_retired_with_history(
    generated_sources: Generate, media_root: Path
) -> None:
    source = generated_sources()
    run_import(source.main_db.parent, dry_run=False)

    for entry in rows(source.archive_db, "SELECT * FROM archived_items"):
        retired = part(entry["item_type"], entry["source_item_id"])
        assert retired.status() == PartStatus.RETIRED
        event = retired.events.get(kind=EventKind.RETIRED)
        assert entry["kommentar"] in event.comment
        assert retired.loans.exists()  # Ausleihen aus der Zeit vor dem Archivieren
        assert LegacyReportItem.objects.filter(part=retired).exists()


def test_gal_files_are_stored_once_and_links_kept(
    generated_sources: Generate, media_root: Path
) -> None:
    run_import(generated_sources().main_db.parent, dry_run=False)

    files = Document.objects.exclude(file="")
    assert files.exists()
    assert all((media_root / str(doc.file.name)).is_file() for doc in files)
    assert Document.objects.filter(url="https://example.org/gal/testmodell.pdf").exists()
    assert not part("product", 1).documents.filter(url__startswith="javascript").exists()


# --- Ausleihen ---


def test_loans_with_events_signatures_and_people(
    generated_sources: Generate, media_root: Path
) -> None:
    source = generated_sources()
    run_import(source.main_db.parent, dry_run=False)

    returned = rows(
        source.main_db,
        "SELECT * FROM verleih_planung WHERE status = 'returned' ORDER BY id LIMIT 1",
    )[0]
    booking = Booking.objects.get(legacy_id=returned["id"])
    loan = booking.loans.get()
    out = loan.events.get(kind=LoanEventKind.CHECKED_OUT)
    back = loan.events.get(kind=LoanEventKind.RETURNED)
    assert loan.status == "returned"
    assert (loan.start.isoformat(), loan.end.isoformat()) == (
        returned["von_datum"],
        returned["rueckgabe_datum"],
    )
    assert (out.actor_name, back.actor_name) == (
        returned["ausgebende_person"],
        returned["ruecknehmende_person"],
    )
    assert out.signature is not None and out.signature.is_legacy
    assert back.comment == (returned["return_comment"] or "")
    assert booking.borrower.contact.name == returned["entleiher"]


def test_overlapping_active_loans_are_rejected(
    generated_sources: Generate, media_root: Path
) -> None:
    report = run_import(generated_sources().main_db.parent, dry_run=False)

    assert any("überschneidet sich mit Ausleihe #" in line for line in report.rejected)


def test_empty_status_follows_legacy_startup_rule(
    generated_sources: Generate, media_root: Path
) -> None:
    source = generated_sources()
    run_import(source.main_db.parent, dry_run=False)

    for row in rows(source.main_db, "SELECT * FROM verleih_planung WHERE status = ''"):
        expected = "lent" if row["signature_data"] else "reserved"
        assert Booking.objects.get(legacy_id=row["id"]).loans.get().status == expected


def test_borrowers_are_merged_by_name_and_contact(fresh_source: Generate, media_root: Path) -> None:
    source = fresh_source("s7", edge_cases=False)
    loans = rows(source.main_db, "SELECT id FROM verleih_planung WHERE status = 'returned'")
    first, second, third = (row["id"] for row in loans[:3])
    for loan_id, phone in ((first, "0000 111"), (second, "0000111"), (third, "0000 999")):
        execute(
            source.main_db,
            "UPDATE verleih_planung SET entleiher = ' noa  PROBE', entleiher_email = '', "
            "entleiher_telefon = ? WHERE id = ?",
            (phone, loan_id),
        )

    run_import(source.main_db.parent, dry_run=False)

    borrower = Booking.objects.get(legacy_id=first).borrower
    assert Booking.objects.get(legacy_id=second).borrower == borrower
    assert Booking.objects.get(legacy_id=third).borrower != borrower
    assert Borrower.objects.filter(contact__name="noa  PROBE").count() == 2


def test_active_loan_of_archived_part_is_rejected(fresh_source: Generate, media_root: Path) -> None:
    source = fresh_source("s7")
    archived = rows(source.archive_db, "SELECT * FROM archived_items")[0]
    execute(
        source.main_db,
        "UPDATE verleih_planung SET status = 'reserved', von_datum = '2099-01-01', "
        "rueckgabe_datum = '2099-01-02' WHERE item_type = ? AND item_id = ?",
        (archived["item_type"], archived["source_item_id"]),
    )

    report = run_import(source.main_db.parent, dry_run=False)

    assert any("aktive Ausleihe des ausgesonderten Teils" in line for line in report.rejected)


# --- Altberichte ---


def test_reports_are_kept_verbatim_and_linked(
    generated_sources: Generate, media_root: Path
) -> None:
    source = generated_sources()
    run_import(source.main_db.parent, dry_run=False)

    header = rows(source.main_db, "SELECT * FROM psa_pruefungsberichte ORDER BY id LIMIT 1")[0]
    report = LegacyReport.objects.get(legacy_id=header["id"])
    assert (report.created_at_raw, report.inspector_name, report.signature_data) == (
        header["created_at"],
        header["sachkundiger_name"],
        header["signature_data"],
    )
    assert report.confirmed is True
    for item in rows(source.main_db, "SELECT * FROM psa_pruefungsbericht_items"):
        stored = LegacyReportItem.objects.get(legacy_id=item["id"])
        assert (stored.action, stored.next_due_raw) == (
            item["aktion"] or "",
            item["naechste_pruefung_am"] or "",
        )
        if stored.part is not None:
            assert (stored.part.legacy_kind, stored.part.legacy_id) == (
                item["item_type"],
                item["item_id"],
            )


def test_reports_have_no_effect_on_parts(generated_sources: Generate, media_root: Path) -> None:
    """Altberichte sind Nachweis; den Stand setzt die Übernahme-Runde."""
    run_import(generated_sources().main_db.parent, dry_run=False)

    assert not LoanEvent.objects.filter(signature__isnull=False, signature__origin="app").exists()
    assert Signature.objects.exclude(origin="legacy").count() == 0
    assert all(
        event.inspection_result is None or event.kind == EventKind.RELEASED
        for event in Part.objects.get(legacy_kind="product", legacy_id=1).events.all()
    )


def test_borrower_of_rejected_loan_is_not_reused(fresh_source: Generate, media_root: Path) -> None:
    """Eine abgelehnte Ausleihe rollt ihren neuen Entleiher zurück; spätere legen ihn neu an."""
    source = fresh_source("s7", edge_cases=False)
    first, second, third = (
        r["id"] for r in rows(source.main_db, "SELECT id FROM verleih_planung")[:3]
    )
    for loan_id, product, start, end, status, name in (
        (first, 1, "2099-01-01", "2099-01-05", "reserved", "Kim Beispiel"),
        (second, 1, "2099-01-03", "2099-01-04", "reserved", "Robin Neu"),
        (third, 2, "2001-01-01", "2001-01-02", "returned", "Robin Neu"),
    ):
        execute(
            source.main_db,
            "UPDATE verleih_planung SET item_type = 'product', item_id = ?, von_datum = ?, "
            "rueckgabe_datum = ?, status = ?, entleiher = ?, entleiher_email = 'x@example.org' "
            "WHERE id = ?",
            (product, start, end, status, name, loan_id),
        )

    report = run_import(source.main_db.parent, dry_run=False)

    assert any(line.startswith(f"Ausleihe #{second}: überschneidet") for line in report.rejected)
    assert Booking.objects.get(legacy_id=third).borrower.contact.name == "Robin Neu"


def test_too_long_fields_are_rejected_not_truncated(
    fresh_source: Generate, media_root: Path
) -> None:
    source = fresh_source("s7", edge_cases=False)
    execute(source.main_db, "UPDATE products SET produktname = ? WHERE id = 1", ("x" * 201,))

    report = run_import(source.main_db.parent, dry_run=False)

    assert "Produkt #1: Produktname länger als 200 Zeichen" in report.rejected
    assert not Part.objects.filter(legacy_kind="product", legacy_id=1).exists()
