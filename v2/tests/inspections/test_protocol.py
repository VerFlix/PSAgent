"""Prüfprotokoll als PDF zum Signieren (Parität 8, #38 Schritt 1).

Festlegungen (Felix, 21.10.2026): je Teil mit Stammdaten; entsteht beim Abschließen und wird
gespeichert, mit dem Kettenkopf von diesem Moment; festes leeres Signaturfeld; herunterladen
darf nur der Prüfer der Runde.
"""

import io
import re

import pytest
from django.test import Client
from django.urls import reverse
from pyhanko.pdf_utils.reader import PdfFileReader
from pyhanko.sign.fields import enumerate_sig_fields
from pypdf import PdfReader

from accounts.models import User
from core import chain
from core.append_only import AppendOnlyError
from core.models import ChainLink
from inspections import protocol, services
from inspections.models import Outcome, RoundEventKind, RoundProtocol
from tests.helpers import adopt_inspection, make_inspector
from tests.inspections.conftest import MakePart, in_days

pytestmark = pytest.mark.django_db


def text_of(pdf: bytes) -> str:
    reader = PdfReader(io.BytesIO(pdf))
    return re.sub(r"\s+", " ", " ".join(page.extract_text() for page in reader.pages))


def closed_round(inspector: User, make_part: MakePart) -> RoundProtocol:
    round_ = services.start_round(actor=inspector)
    helm = make_part(
        designation="Helm",
        ei="TST-PROT1",
        manufacturer="Beispielwerk (Testdaten)",
        model_name="Kopfschutz 1",
        serial_number="SN-0815",
    )
    gurt = make_part(designation="Gurt", ei="TST-PROT2")
    services.record_result(round_, helm, Outcome.PASSED, actor=inspector, next_due=in_days(365))
    services.record_result(round_, gurt, Outcome.DEFECT, actor=inspector, comment="Naht offen")
    services.close_round(round_, actor=inspector)
    return RoundProtocol.objects.get(round=round_)


def test_parity_8_closing_creates_protocol_with_master_data(
    inspector: User, make_part: MakePart
) -> None:
    entry = closed_round(inspector, make_part)

    text = text_of(bytes(entry.pdf))
    for expected in [
        "Prüfprotokoll",
        inspector.get_full_name(),
        "TST-PROT1",
        "Beispielwerk (Testdaten)",
        "Kopfschutz 1",
        "SN-0815",
        "TST-PROT2",
        "Naht offen",
        Outcome.DEFECT.label,
        f"{in_days(365):%d.%m.%Y}",
    ]:
        assert expected in text, expected
    assert entry.sha256 == protocol.sha256(bytes(entry.pdf))


def test_parity_8_protocol_carries_chain_head_after_closing(
    inspector: User, make_part: MakePart
) -> None:
    """#38, Ergänzung 19.10.2026: Das Protokoll enthält Nummer und Hash des Kettenkopfs."""
    entry = closed_round(inspector, make_part)
    closed = entry.round.events.get(kind=RoundEventKind.CLOSED)
    link = ChainLink.objects.get(table="inspections.RoundEvent", row_pk=str(closed.pk))

    assert (entry.chain_seq, entry.chain_hash) == (link.seq, link.hash)
    text = text_of(bytes(entry.pdf)).replace(" ", "")
    assert entry.chain_hash in text and entry.content_sha256 in text
    assert entry.content_sha256 == closed.content_sha256
    assert chain.verify() == []


def test_protocol_has_empty_signature_field_on_last_page(
    inspector: User, make_part: MakePart
) -> None:
    entry = closed_round(inspector, make_part)

    reader = PdfFileReader(io.BytesIO(bytes(entry.pdf)))
    fields = list(enumerate_sig_fields(reader))

    assert [(name, value) for name, value, _ in fields] == [(protocol.SIGNATURE_FIELD, None)]
    widget = fields[0][2].get_object()
    pages = reader.root["/Pages"]["/Kids"]
    assert widget["/P"].get_object() == pages[len(pages) - 1].get_object()
    assert [round(float(v)) for v in widget["/Rect"]] == list(protocol.field_box())


def test_protocol_is_append_only(inspector: User, make_part: MakePart) -> None:
    closed_round(inspector, make_part)

    with pytest.raises(AppendOnlyError):
        RoundProtocol.objects.all().update(chain_seq=1)
    with pytest.raises(AppendOnlyError):
        RoundProtocol.objects.all().delete()


def test_legacy_round_gets_no_protocol(make_part: MakePart) -> None:
    adopt_inspection([make_part()], next_due=in_days(100))

    assert not RoundProtocol.objects.exists()


# --- Herunterladen ---


def test_inspector_downloads_own_protocol(
    client: Client, inspector: User, make_part: MakePart
) -> None:
    entry = closed_round(inspector, make_part)
    client.force_login(inspector)

    page = client.get(reverse("inspections:round_mode")).content.decode()
    response = client.get(reverse("inspections:round_protocol", args=[entry.round.pk]))

    assert "Prüfprotokoll zum Signieren" in page
    assert response.status_code == 200
    assert response["Content-Type"] == "application/pdf"
    assert "no-store" in response["Cache-Control"]
    assert f"Pruefprotokoll_Runde_{entry.round.pk}.pdf" in response["Content-Disposition"]
    assert response.content == bytes(entry.pdf)


def test_other_inspector_cannot_download_protocol(
    client: Client, inspector: User, make_part: MakePart
) -> None:
    """Verbotener Fall: nur der Prüfer der Runde, auch kein anderer Sachkundiger (404)."""
    entry = closed_round(inspector, make_part)
    client.force_login(make_inspector())

    response = client.get(reverse("inspections:round_protocol", args=[entry.round.pk]))

    assert response.status_code == 404


def test_long_protocol_has_field_on_last_page_only(inspector: User, make_part: MakePart) -> None:
    round_ = services.start_round(actor=inspector)
    for _ in range(14):
        services.record_result(
            round_, make_part(), Outcome.PASSED, actor=inspector, next_due=in_days(365)
        )
    services.close_round(round_, actor=inspector)
    pdf = bytes(RoundProtocol.objects.get(round=round_).pdf)

    reader = PdfFileReader(io.BytesIO(pdf))
    pages = reader.root["/Pages"]["/Kids"]
    [(_, _, widget)] = list(enumerate_sig_fields(reader))

    assert len(pages) > 1
    assert widget.get_object()["/P"].get_object() == pages[len(pages) - 1].get_object()
    assert text_of(pdf).count("Signatur PSA-Sachkundige") == 1
