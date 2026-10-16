"""Nacherfassung nach dem Notbetrieb (Parität 22, Konzept Abschnitt 7, Teil 1: Sperrungen).

1. Formularnummer eingeben, Scan hochladen (PDF oder Fotos); der Scan wird als PDF mit
   Prüfsumme gespeichert.
2. Die Daten mit dem Originaldatum eintragen; der Eintrag trägt „nachgetragen von X am Y“ und
   verweist auf das Formular.
3. Fehlende Formularnummern landen auf der Klärungsliste für PSA-Prüfer oder Admin.
"""

import hashlib
import io
import struct
import zlib
from datetime import date, timedelta

import pytest
from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role, User
from emergency import backfill
from emergency.binder import SHEETS_PER_BINDER, create_binder
from emergency.models import Binder, Clarification, FormSheet, LockEntry, PaperForm
from inventory.models import EventKind, PartEvent
from inventory.services import record_event
from tests.helpers import make_inspector
from tests.lending.conftest import MakePart

pytestmark = pytest.mark.django_db
SCAN = b"%PDF-1.4\n% Scan Sperrformular, kuenstliche Testdaten\n%%EOF\n"


def png() -> bytes:
    """Kleinstes gültiges PNG (1 x 1 Pixel), als Ersatz für ein Handyfoto."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    header = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    pixels = zlib.compress(b"\x00\xff\x00\x00")
    chunks = chunk(b"IHDR", header) + chunk(b"IDAT", pixels) + chunk(b"IEND", b"")
    return b"\x89PNG\r\n\x1a\n" + chunks


@pytest.fixture
def expert() -> User:
    return make_inspector()


def lock_row(ei: str, *, reason: str = "Naht offen", on: date | None = None) -> dict[str, object]:
    day = on or timezone.localdate()
    return {"ei": ei, "reason": reason, "locked_on": day, "locked_by_name": "Testperson Sperrer"}


def binder_printed(days_ago: int) -> Binder:
    """Mappe, die vor `days_ago` Tagen gedruckt wurde (Nummern wie von `create_binder`)."""
    binder = Binder.objects.create(
        created_at=timezone.now() - timedelta(days=days_ago), sha256="0" * 64
    )
    for kind, count in SHEETS_PER_BINDER.items():
        start = FormSheet.objects.filter(kind=kind).count()
        FormSheet.objects.bulk_create(
            FormSheet(binder=binder, kind=kind, number=start + n) for n in range(1, count + 1)
        )
    return binder


# --- Dienst: Sperrungen nacherfassen ---


def test_parity_22_lock_backfilled_with_original_date(make_part: MakePart, lender: User) -> None:
    part = make_part()
    binder_printed(days_ago=10)
    ei = part.components.get().ei
    on = timezone.localdate() - timedelta(days=6)

    form = backfill.capture_locks(
        "S-00001", [(SCAN, "scan.pdf")], [lock_row(ei, on=on)], actor=lender
    )

    part.refresh_from_db()
    assert part.is_locked()
    event = PartEvent.objects.get(part=part, kind=EventKind.LOCKED)
    assert timezone.localtime(event.at).date() == on
    assert event.actor == lender
    assert "Naht offen" in event.comment
    assert "Formular S-00001" in event.comment and "Testperson Sperrer" in event.comment
    assert f"nachgetragen von {lender.get_full_name()} am" in event.comment
    assert form.sheet.label == "S-00001" and form.recorded_by == lender
    assert LockEntry.objects.get(paper_form=form).part_event == event


def test_parity_22_scan_stored_with_checksum(make_part: MakePart, lender: User) -> None:
    part = make_part()
    create_binder(actor=None)

    form = backfill.capture_locks(
        "s-00001", [(SCAN, "scan.pdf")], [lock_row(part.components.get().ei)], actor=lender
    )

    assert form.scan_sha256 == hashlib.sha256(SCAN).hexdigest()
    assert backfill.scan_path(form).read_bytes() == SCAN


def test_parity_22_photos_become_one_pdf(make_part: MakePart, lender: User) -> None:
    part = make_part()
    create_binder(actor=None)

    form = backfill.capture_locks(
        "S-00001",
        [(png(), "seite1.png"), (png(), "seite2.png")],
        [lock_row(part.components.get().ei)],
        actor=lender,
    )

    content = backfill.scan_path(form).read_bytes()
    assert content.startswith(b"%PDF-")
    from pypdf import PdfReader

    assert len(PdfReader(io.BytesIO(content)).pages) == 2


@pytest.mark.parametrize(
    ("number", "message"),
    [
        ("S-00099", "nicht vergeben"),
        ("X-00001", "Formularnummer"),
        ("P-00001", "Phase 3"),
    ],
)
def test_unknown_or_unsupported_number_is_refused(
    number: str, message: str, make_part: MakePart, lender: User
) -> None:
    part = make_part()
    create_binder(actor=None)

    with pytest.raises(backfill.BackfillError, match=message):
        backfill.capture_locks(
            number, [(SCAN, "scan.pdf")], [lock_row(part.components.get().ei)], actor=lender
        )

    assert PaperForm.objects.count() == 0


def test_form_is_captured_only_once(make_part: MakePart, lender: User) -> None:
    first, second = make_part(), make_part()
    create_binder(actor=None)
    backfill.capture_locks(
        "S-00001", [(SCAN, "scan.pdf")], [lock_row(first.components.get().ei)], actor=lender
    )

    with pytest.raises(backfill.BackfillError, match="bereits erfasst"):
        backfill.capture_locks(
            "S-00001", [(SCAN, "scan.pdf")], [lock_row(second.components.get().ei)], actor=lender
        )

    second.refresh_from_db()
    assert not second.is_locked()


@pytest.mark.parametrize("content", [b"<html>", b"GIF89a", b""], ids=["html", "gif", "leer"])
def test_scan_must_be_pdf_or_photo(content: bytes, make_part: MakePart, lender: User) -> None:
    part = make_part()
    create_binder(actor=None)

    with pytest.raises(backfill.BackfillError, match="PDF oder Foto"):
        backfill.capture_locks(
            "S-00001", [(content, "scan")], [lock_row(part.components.get().ei)], actor=lender
        )

    assert PaperForm.objects.count() == 0


def test_unknown_ei_rolls_back_everything(make_part: MakePart, lender: User) -> None:
    part = make_part()
    create_binder(actor=None)

    with pytest.raises(backfill.BackfillError, match="TST-GIBTSNICHT"):
        backfill.capture_locks(
            "S-00001",
            [(SCAN, "scan.pdf")],
            [lock_row(part.components.get().ei), lock_row("TST-GIBTSNICHT")],
            actor=lender,
        )

    part.refresh_from_db()
    assert not part.is_locked() and PaperForm.objects.count() == 0


def test_ei_matches_without_case_and_spaces(make_part: MakePart, lender: User) -> None:
    part = make_part()
    create_binder(actor=None)
    ei = part.components.get().ei

    backfill.capture_locks(
        "S-00001", [(SCAN, "scan.pdf")], [lock_row(f" {ei.lower()} ")], actor=lender
    )

    part.refresh_from_db()
    assert part.is_locked()


def test_parity_22_already_locked_part_becomes_clarification(
    make_part: MakePart, lender: User, expert: User
) -> None:
    """Ist das Teil schon gesperrt, wird die Zeile mit Grund gespeichert und geklärt."""
    part = make_part()
    record_event(part, EventKind.LOCKED, actor=None, comment="vorher gesperrt")
    create_binder(actor=None)

    form = backfill.capture_locks(
        "S-00001", [(SCAN, "scan.pdf")], [lock_row(part.components.get().ei)], actor=lender
    )

    entry = LockEntry.objects.get(paper_form=form)
    assert entry.part_event is None and "bereits gesperrt" in entry.note
    cases = backfill.open_cases()
    assert [case.key for case in cases] == [f"sperre:{entry.pk}"]


def test_backfill_needs_lend_right(make_part: MakePart) -> None:
    """Verbotener Fall: Viewer erfassen nichts nach."""
    part = make_part()
    create_binder(actor=None)

    with pytest.raises(PermissionDenied):
        backfill.capture_locks(
            "S-00001",
            [(SCAN, "scan.pdf")],
            [lock_row(part.components.get().ei)],
            actor=make_inspector(role=Role.VIEWER, qualified=False),
        )

    assert PaperForm.objects.count() == 0


# --- Klärungsliste ---


def capture(number: str, part_ei: str, actor: User) -> PaperForm:
    return backfill.capture_locks(number, [(SCAN, "scan.pdf")], [lock_row(part_ei)], actor=actor)


def test_parity_22_missing_form_numbers(make_part: MakePart, lender: User) -> None:
    parts = [make_part() for _ in range(3)]
    create_binder(actor=None)
    capture("S-00001", parts[0].components.get().ei, lender)
    capture("S-00004", parts[1].components.get().ei, lender)

    keys = [case.key for case in backfill.open_cases()]

    assert keys == ["luecke:S-00002", "luecke:S-00003"]


def test_gaps_only_within_one_binder(make_part: MakePart, lender: User) -> None:
    """Nummern einer neueren Mappe machen keine Lücke in der älteren."""
    first, second = make_part(), make_part()
    create_binder(actor=None)  # S-00001 … S-00005
    create_binder(actor=None)  # S-00006 … S-00010
    capture("S-00001", first.components.get().ei, lender)
    capture("S-00006", second.components.get().ei, lender)

    assert backfill.open_cases() == []


def test_parity_22_expert_resolves_case(make_part: MakePart, lender: User, expert: User) -> None:
    parts = [make_part() for _ in range(2)]
    create_binder(actor=None)
    capture("S-00001", parts[0].components.get().ei, lender)
    capture("S-00003", parts[1].components.get().ei, lender)

    backfill.resolve("luecke:S-00002", note="Blatt verschrieben, liegt im Fach", actor=expert)

    assert backfill.open_cases() == []
    clarification = Clarification.objects.get()
    assert clarification.resolved_by == expert and "verschrieben" in clarification.note


def test_resolve_needs_note_and_open_case(make_part: MakePart, expert: User) -> None:
    with pytest.raises(backfill.BackfillError, match="Begründung"):
        backfill.resolve("luecke:S-00002", note=" ", actor=expert)
    with pytest.raises(backfill.BackfillError, match="kein offener"):
        backfill.resolve("luecke:S-00002", note="erledigt", actor=expert)


@pytest.mark.parametrize("role", [Role.TRAINER, Role.LENDER, Role.VIEWER])
def test_only_expert_or_admin_resolves(role: Role, make_part: MakePart, lender: User) -> None:
    """Verbotener Fall: Die Klärungsliste bearbeiten nur PSA-Prüfer oder Admin."""
    parts = [make_part() for _ in range(2)]
    create_binder(actor=None)
    capture("S-00001", parts[0].components.get().ei, lender)
    capture("S-00003", parts[1].components.get().ei, lender)

    with pytest.raises(PermissionDenied):
        backfill.resolve(
            "luecke:S-00002", note="erledigt", actor=make_inspector(role=role, qualified=False)
        )

    assert Clarification.objects.count() == 0


# --- Oberfläche ---


def test_parity_22_capture_via_form(client: Client, lender: User, make_part: MakePart) -> None:
    part = make_part()
    create_binder(actor=None)
    client.force_login(lender)
    ei = part.components.get().ei

    page = client.get(reverse("emergency:backfill_locks")).content.decode()
    response = client.post(
        reverse("emergency:backfill_locks"),
        {
            "number": "S-00001",
            "scan": [SimpleUploadedFile("scan.pdf", SCAN, content_type="application/pdf")],
            "rows-TOTAL_FORMS": "2",
            "rows-INITIAL_FORMS": "0",
            "rows-0-ei": ei,
            "rows-0-reason": "Naht offen",
            "rows-0-locked_on": timezone.localdate().isoformat(),
            "rows-0-locked_by_name": "Testperson Sperrer",
        },
        follow=True,
    )

    assert 'capture="environment"' in page
    assert 'accept="application/pdf,image/jpeg,image/png"' in page
    assert "Formular S-00001 nacherfasst" in response.content.decode()
    part.refresh_from_db()
    assert part.is_locked()


def test_capture_form_shows_errors(client: Client, lender: User, make_part: MakePart) -> None:
    make_part()
    create_binder(actor=None)
    client.force_login(lender)

    response = client.post(
        reverse("emergency:backfill_locks"),
        {
            "number": "S-00001",
            "scan": [SimpleUploadedFile("scan.pdf", SCAN, content_type="application/pdf")],
            "rows-TOTAL_FORMS": "1",
            "rows-INITIAL_FORMS": "0",
            "rows-0-ei": "TST-GIBTSNICHT",
            "rows-0-reason": "Naht offen",
            "rows-0-locked_on": timezone.localdate().isoformat(),
            "rows-0-locked_by_name": "Testperson Sperrer",
        },
    )

    assert response.status_code == 200
    assert "TST-GIBTSNICHT" in response.content.decode()
    assert PaperForm.objects.count() == 0


def test_parity_22_clarification_page(
    client: Client, lender: User, expert: User, make_part: MakePart
) -> None:
    parts = [make_part() for _ in range(2)]
    create_binder(actor=None)
    capture("S-00001", parts[0].components.get().ei, lender)
    form = capture("S-00003", parts[1].components.get().ei, lender)
    client.force_login(expert)

    page = client.get(reverse("emergency:clarifications")).content.decode()
    client.post(
        reverse("emergency:clarifications"),
        {"key": "luecke:S-00002", "note": "Blatt verschrieben"},
    )
    after = client.get(reverse("emergency:clarifications")).content.decode()
    scan = client.get(reverse("emergency:scan", args=[form.pk]))

    assert "Formular S-00002 fehlt" in page
    assert "Keine offenen Fälle." in after and "Blatt verschrieben" in after
    assert scan["Content-Type"] == "application/pdf"


def test_lender_has_no_clarification_page(client: Client, lender: User) -> None:
    client.force_login(lender)

    assert client.get(reverse("emergency:clarifications")).status_code == 403


# --- Sicherheit: Datum, spätere Vorgänge, Fotogröße ---


@pytest.mark.parametrize(
    ("days", "message"),
    [(1, "Zukunft"), (-11, "vor dem Druck")],
    ids=["morgen", "vor-der-mappe"],
)
def test_form_date_between_print_and_today(
    days: int, message: str, make_part: MakePart, lender: User
) -> None:
    """Rückdatieren über das Formular ist begrenzt: nicht vor der Mappe, nicht nach heute."""
    part = make_part()
    binder_printed(days_ago=10)
    row = lock_row(part.components.get().ei, on=timezone.localdate() + timedelta(days=days))

    with pytest.raises(backfill.BackfillError, match=message):
        backfill.capture_locks("S-00001", [(SCAN, "scan.pdf")], [row], actor=lender)

    assert PaperForm.objects.count() == 0


def test_later_status_event_becomes_clarification(
    make_part: MakePart, lender: User, expert: User
) -> None:
    """Ein rückdatierter Vorgang darf die Reihenfolge späterer Vorgänge nicht verändern."""
    part = make_part()
    binder_printed(days_ago=10)
    record_event(part, EventKind.LOCKED, actor=None, comment="nach dem Ausfall gesperrt")
    record_event(part, EventKind.UNLOCKED, actor=expert, comment="geprüft, in Ordnung")
    row = lock_row(part.components.get().ei, on=timezone.localdate() - timedelta(days=5))

    form = backfill.capture_locks("S-00001", [(SCAN, "scan.pdf")], [row], actor=lender)

    part.refresh_from_db()
    assert not part.is_locked()
    entry = LockEntry.objects.get(paper_form=form)
    assert entry.part_event is None and "spätere" in entry.note
    assert [case.key for case in backfill.open_cases()] == [f"sperre:{entry.pk}"]


def huge_png() -> bytes:
    """PNG-Kopf, der 100 000 x 100 000 Bildpunkte ankündigt (Pixelbombe)."""
    content = bytearray(png())
    content[16:24] = (100_000).to_bytes(4) + (100_000).to_bytes(4)
    return bytes(content)


def huge_jpeg() -> bytes:
    """JPEG-Kopf (SOI, APP0, SOF0) mit 60 000 x 60 000 Bildpunkten."""
    app0 = b"\xff\xe0" + (16).to_bytes(2) + b"JFIF\x00" + bytes(9)
    size = (60_000).to_bytes(2) * 2
    sof0 = b"\xff\xc0" + (11).to_bytes(2) + b"\x08" + size + b"\x01\x01\x11\x00"
    return b"\xff\xd8" + app0 + sof0


@pytest.mark.parametrize("content", [huge_png(), huge_jpeg()], ids=["png", "jpeg"])
def test_oversized_photo_is_refused_before_decoding(
    content: bytes, make_part: MakePart, lender: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_compile(*args: object, **kwargs: object) -> bytes:
        raise AssertionError("Typst darf die Pixelbombe nicht entpacken")

    part = make_part()
    create_binder(actor=None)
    row = lock_row(part.components.get().ei)
    monkeypatch.setattr("emergency.backfill.typst.compile", no_compile)

    with pytest.raises(backfill.BackfillError, match="zu groß"):
        backfill.capture_locks("S-00001", [(content, "foto")], [row], actor=lender)


def test_photo_without_size_is_refused(make_part: MakePart, lender: User) -> None:
    part = make_part()
    create_binder(actor=None)
    row = lock_row(part.components.get().ei)

    with pytest.raises(backfill.BackfillError, match="nicht lesen"):
        backfill.capture_locks(
            "S-00001", [(b"\xff\xd8\xff\xd9", "kaputt.jpg")], [row], actor=lender
        )


def test_photos_together_are_limited(
    make_part: MakePart, lender: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Auch viele Fotos knapp unter der Grenze je Foto werden vor dem Entpacken abgewiesen."""

    def no_compile(*args: object, **kwargs: object) -> bytes:
        raise AssertionError("Typst darf die Fotos nicht entpacken")

    content = bytearray(png())
    content[16:24] = (7_000).to_bytes(4) + (8_000).to_bytes(4)  # 56 Megapixel
    part = make_part()
    create_binder(actor=None)
    row = lock_row(part.components.get().ei)
    monkeypatch.setattr("emergency.backfill.typst.compile", no_compile)

    with pytest.raises(backfill.BackfillError, match="zusammen zu groß"):
        backfill.capture_locks(
            "S-00001", [(bytes(content), f"foto{n}.png") for n in range(3)], [row], actor=lender
        )
