"""Signiertes Prüfprotokoll hochladen und prüfen (Parität 8, #38 Schritt 2).

Festlegungen (Felix, 21.10.2026): nur der Prüfer der Runde; Zertifikat und Sachkunde zum
Zeitpunkt des Hochladens; ohne Zeitstempel wird nichts gespeichert; abgelehnte Versuche stehen
bei der Runde. Signiert wird mit künstlichen YubiKeys (`tests/signing.py`).
"""

from io import BytesIO, StringIO
from unittest import mock

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import CommandError, call_command
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from pyhanko.pdf_utils import generic
from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
from pyhanko.pdf_utils.reader import PdfFileReader
from pyhanko.sign import fields

from accounts import certificates
from accounts.models import QualificationRevocation, User
from core.append_only import AppendOnlyError
from inspections import services
from inspections.models import (
    InspectionRound,
    Outcome,
    RoundProtocol,
    RoundState,
    SignatureRejection,
    SignedProtocol,
)
from inspections.signature import SignatureError
from tests.helpers import make_inspector, tamper
from tests.inspections.conftest import MakePart, in_days
from tests.signing import sign, sign_pdf, signed_protocol, signing_environment, yubikey_of

pytestmark = pytest.mark.django_db


def closed(inspector: User, make_part: MakePart) -> InspectionRound:
    round_ = services.start_round(actor=inspector)
    services.record_result(
        round_, make_part(), Outcome.PASSED, actor=inspector, next_due=in_days(365)
    )
    services.close_round(round_, actor=inspector)
    return round_


def protocol_of(round_: InspectionRound) -> bytes:
    return bytes(RoundProtocol.objects.get(round=round_).pdf)


def refused(round_: InspectionRound, actor: User, pdf: bytes, message: str) -> None:
    """Abgelehnt mit Grund, als Vorgang festgehalten, und die Runde bleibt unverändert."""
    with pytest.raises(SignatureError, match=message):
        sign(round_, actor=actor, pdf=pdf)
    assert round_.state() == RoundState.CLOSED
    assert not SignedProtocol.objects.filter(round=round_).exists()
    rejection = SignatureRejection.objects.filter(round=round_).latest("at")
    assert rejection.actor == actor and rejection.reason


# --- Erlaubt ---


def test_parity_8_signed_protocol_makes_round_effective(
    inspector: User, make_part: MakePart
) -> None:
    round_ = closed(inspector, make_part)
    pdf = signed_protocol(round_)

    sign(round_, actor=inspector, pdf=pdf)

    entry = SignedProtocol.objects.get(round=round_)
    assert round_.state() == RoundState.SIGNED
    assert bytes(entry.pdf).startswith(pdf) and len(bytes(entry.pdf)) > len(pdf)
    assert entry.certificate.user == inspector
    assert entry.stamped_at is not None and entry.signed_at <= timezone.now()
    reader = PdfFileReader(BytesIO(bytes(entry.pdf)))
    assert len(reader.embedded_regular_signatures) == 1
    assert len(reader.embedded_timestamp_signatures) == 1
    part = round_.results.get().part
    assert services.next_inspection_due(part) == in_days(365)
    assert not SignatureRejection.objects.exists()


def test_signed_protocols_are_append_only(inspector: User, make_part: MakePart) -> None:
    round_ = closed(inspector, make_part)
    with pytest.raises(SignatureError):
        sign(round_, actor=inspector, pdf=b"kein PDF")
    sign(round_, actor=inspector)

    for model in (SignedProtocol, SignatureRejection):
        with pytest.raises(AppendOnlyError):
            model.objects.all().delete()


# --- Verboten: Datei ---


def test_unsigned_protocol_is_refused(inspector: User, make_part: MakePart) -> None:
    round_ = closed(inspector, make_part)

    refused(round_, inspector, protocol_of(round_), "nicht das signierte Prüfprotokoll")


def test_protocol_of_another_round_is_refused(inspector: User, make_part: MakePart) -> None:
    first = closed(inspector, make_part)
    second = closed(inspector, make_part)

    refused(second, inspector, signed_protocol(first), "nicht das signierte Prüfprotokoll")


def test_garbage_is_refused(inspector: User, make_part: MakePart) -> None:
    round_ = closed(inspector, make_part)

    refused(round_, inspector, protocol_of(round_) + b"Unsinn", "kein lesbares PDF|Signatur")


def test_change_after_signing_is_refused(inspector: User, make_part: MakePart) -> None:
    round_ = closed(inspector, make_part)
    writer = IncrementalPdfFileWriter(BytesIO(signed_protocol(round_)))
    writer.root["/Lang"] = generic.TextStringObject("en")
    writer.update_root()  # type: ignore[no-untyped-call]
    out = BytesIO()
    writer.write(out)  # type: ignore[no-untyped-call]

    refused(round_, inspector, out.getvalue(), "noch verändert")


def test_change_of_content_with_the_signature_is_refused(
    inspector: User, make_part: MakePart
) -> None:
    """Angriff: Mit der Signatur zugleich den Seiteninhalt ersetzen (Signatur selbst gültig)."""
    round_ = closed(inspector, make_part)
    writer = IncrementalPdfFileWriter(BytesIO(protocol_of(round_)))
    page = writer.root["/Pages"]["/Kids"][0].get_object()
    page["/Contents"] = writer.add_object(
        generic.StreamObject(stream_data=b"BT /F1 30 Tf 100 700 Td (alles gut) Tj ET")
    )
    writer.update_container(page)
    out = BytesIO()
    writer.write(out)  # type: ignore[no-untyped-call]
    with signing_environment():
        key = yubikey_of(inspector)
    # Inhalt und Signatur kommen hier in zwei Nachträgen; beides wird abgelehnt.
    tampered = sign_pdf(out.getvalue(), key)

    refused(round_, inspector, tampered, "verändert|mehr geändert")


def test_signature_in_other_field_is_refused(inspector: User, make_part: MakePart) -> None:
    round_ = closed(inspector, make_part)
    writer = IncrementalPdfFileWriter(BytesIO(protocol_of(round_)))
    fields.append_signature_field(
        writer, fields.SigFieldSpec(sig_field_name="Anderes", on_page=0, box=(10, 10, 50, 50))
    )
    out = BytesIO()
    writer.write(out)  # type: ignore[no-untyped-call]
    with signing_environment():
        key = yubikey_of(inspector)

    refused(round_, inspector, sign_pdf(out.getvalue(), key, field="Anderes"), "Feld")


def test_broken_signature_is_refused(inspector: User, make_part: MakePart) -> None:
    """Ein Byte im signierten Nachtrag geändert (Titel in den Metadaten): Signatur passt nicht."""
    round_ = closed(inspector, make_part)
    original = protocol_of(round_)
    pdf = bytearray(signed_protocol(round_))
    position = pdf.index(b"Runde", len(original)) + len(b"Rund")
    pdf[position] = ord("x")

    refused(round_, inspector, bytes(pdf), "ungültig|nicht prüfen|lesbar")


# --- Verboten: Zertifikat und Person ---


def test_unlisted_certificate_is_refused(inspector: User, make_part: MakePart) -> None:
    round_ = closed(inspector, make_part)
    with signing_environment() as yubico:
        stranger = yubico.yubikey(4711)

    refused(round_, inspector, signed_protocol(round_, key=stranger), "nicht in der Liste")


def test_revoked_certificate_is_refused(inspector: User, make_part: MakePart) -> None:
    round_ = closed(inspector, make_part)
    pdf = signed_protocol(round_)
    certificates.revoke(inspector.signing_certificates.get(), actor=None, reason="YubiKey verloren")

    refused(round_, inspector, pdf, "widerrufen")


def test_certificate_of_another_inspector_is_refused(inspector: User, make_part: MakePart) -> None:
    round_ = closed(inspector, make_part)
    with signing_environment():
        other_key = yubikey_of(make_inspector())

    refused(round_, inspector, signed_protocol(round_, key=other_key), "nicht zum Prüfer")


def test_other_inspector_cannot_upload(inspector: User, make_part: MakePart) -> None:
    """Nur der Prüfer der Runde lädt hoch (Festlegung 21.10.2026)."""
    round_ = closed(inspector, make_part)

    with pytest.raises(services.InspectionError, match="handelt nur"):
        sign(round_, actor=make_inspector(), pdf=signed_protocol(round_))
    assert round_.state() == RoundState.CLOSED


def test_expired_qualification_at_upload_is_refused(inspector: User, make_part: MakePart) -> None:
    """Gegen den Zeitpunkt des Hochladens: Sachkunde danach widerrufen = abgelehnt."""
    round_ = closed(inspector, make_part)
    pdf = signed_protocol(round_)
    QualificationRevocation.objects.create(
        qualification=inspector.qualifications.get(), reason="Test"
    )

    with pytest.raises(Exception, match="Sachkunde"):
        sign(round_, actor=inspector, pdf=pdf)
    assert round_.state() == RoundState.CLOSED


# --- Zeitstempel ---


def test_unreachable_timestamp_service_stores_nothing(inspector: User, make_part: MakePart) -> None:
    round_ = closed(inspector, make_part)
    pdf = signed_protocol(round_)

    class Down:
        async def async_timestamp(self, *args: object, **kwargs: object) -> None:
            raise ConnectionError("Dienst nicht erreichbar (Test)")

    with (
        signing_environment(),
        mock.patch("core.timestamp.stamper", lambda: Down()),
        pytest.raises(SignatureError, match="später erneut"),
    ):
        services.sign_round(round_, actor=inspector, signed_pdf=pdf)

    assert round_.state() == RoundState.CLOSED
    assert not SignedProtocol.objects.exists()
    assert SignatureRejection.objects.get().reason.startswith("Der Zeitstempeldienst")


# --- Kettenköpfe aus signierten Protokollen ---


def test_chain_check_uses_heads_from_signed_protocols(inspector: User, make_part: MakePart) -> None:
    """#38 (Ergänzung 19.10.2026): Eine neu berechnete Kette passt nicht mehr zum Protokoll."""
    round_ = closed(inspector, make_part)
    sign(round_, actor=inspector)
    with signing_environment():
        call_command("kette_pruefen", stdout=StringIO(), stderr=StringIO())
    head = RoundProtocol.objects.get(round=round_)

    tamper(
        "core_chainlink",
        "UPDATE core_chainlink SET hash = %s WHERE seq = %s",
        ["f" * 64, head.chain_seq],
    )

    errors = StringIO()
    with signing_environment(), pytest.raises(CommandError):
        call_command("kette_pruefen", stdout=StringIO(), stderr=errors)
    assert f"Kettenkopf {head.chain_seq} weicht ab" in errors.getvalue()


# --- Oberfläche ---


def test_inspector_uploads_signed_protocol(
    client: Client, inspector: User, make_part: MakePart
) -> None:
    round_ = closed(inspector, make_part)
    pdf = signed_protocol(round_)
    client.force_login(inspector)
    with signing_environment():
        response = client.post(
            reverse("inspections:round_sign", args=[round_.pk]),
            {"signed_pdf": SimpleUploadedFile("signiert.pdf", pdf)},
            follow=True,
        )

    assert "signiert. Die Ergebnisse wirken jetzt" in response.content.decode()
    assert round_.state() == RoundState.SIGNED


def test_refused_upload_shows_reason(client: Client, inspector: User, make_part: MakePart) -> None:
    round_ = closed(inspector, make_part)
    client.force_login(inspector)
    with signing_environment():
        response = client.post(
            reverse("inspections:round_sign", args=[round_.pk]),
            {"signed_pdf": SimpleUploadedFile("leer.pdf", protocol_of(round_))},
            follow=True,
        )

    assert "nicht das signierte Prüfprotokoll" in response.content.decode()
    assert round_.state() == RoundState.CLOSED
