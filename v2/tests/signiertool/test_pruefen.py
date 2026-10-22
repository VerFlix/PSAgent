"""Signier-Tool: signierten Prüfbericht ohne Datenbank prüfen (`signieren pruefen`, #19 Schritt 2).

Festlegungen (Felix, 22.10.2026): für Felix und den Vorstand; dem Zeitstempeldienst wird nur mit
`--tsa-ca` vertraut, die Fingerabdrücke kommen mit `--fingerabdruecke` (gleiches Format wie bei
`kette_pruefen`). Ergebnis unverändert (0), verändert (1), nicht prüfbar (2).

Geprüft wird hier, was ein Bericht aus PSAgent ist: Der Server signiert nicht selbst, also
entsteht der Bericht wie im Betrieb (Signatur wie mit dem YubiKey, Zeitstempel beim Hochladen).
"""

from io import BytesIO
from pathlib import Path

import pytest
from psagent_signieren import check as offline
from psagent_signieren import cli
from pyhanko.pdf_utils import generic
from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter

from accounts.models import User
from core import audit
from inspections import protocol, services
from inspections.models import Outcome, SignedProtocol
from inspections.signature import stamp
from tests.accounts.yubikey import FakeYubico
from tests.core.conftest import make_authority
from tests.inspections.conftest import MakePart, in_days
from tests.signing import (
    _environment,
    sign,
    sign_pdf,
    signed_protocol,
    signing_environment,
    yubikey_of,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def report(inspector: User, make_part: MakePart) -> SignedProtocol:
    """Signierter, hochgeladener Prüfbericht, wie ihn die Seite „Prüfberichte“ liefert."""
    round_ = services.start_round(actor=inspector)
    services.record_result(
        round_, make_part(ei="TST-OFF1"), Outcome.PASSED, actor=inspector, next_due=in_days(365)
    )
    services.close_round(round_, actor=inspector)
    sign(round_, actor=inspector)
    return SignedProtocol.objects.select_related("protocol", "certificate").get(round=round_)


@pytest.fixture
def tsa_ca() -> Path:
    return _environment()[3]


@pytest.fixture
def listed(report: SignedProtocol, tmp_path: Path) -> Path:
    path = tmp_path / "fingerabdruecke.txt"
    path.write_text(f"# Papierliste\n{report.certificate.sha256}  # YubiKey 1\n")
    return path


def run(tmp_path: Path, pdf: bytes, *options: str) -> tuple[int, str]:
    path = tmp_path / "bericht.pdf"
    path.write_bytes(pdf)
    lines: list[str] = []
    code = cli.main(["pruefen", str(path), *options], write=lines.append)
    return code, "\n".join(lines)


# --- unverändert ---


def test_unchanged_report(
    report: SignedProtocol, tsa_ca: Path, listed: Path, tmp_path: Path
) -> None:
    code, text = run(
        tmp_path, bytes(report.pdf), f"--tsa-ca={tsa_ca}", f"--fingerabdruecke={listed}"
    )

    assert code == 0, text
    assert "Ergebnis: unverändert." in text
    assert f"Runde: {report.round_id}" in text
    head = f"{report.protocol.chain_seq}:{report.protocol.chain_hash}"
    assert f"Kettenkopf: {head}" in text
    assert f"kette_pruefen --kopf {head}" in text
    assert f"{report.stamped_at:%d.%m.%Y %H:%M}" in text
    assert report.certificate.sha256 in text


def test_without_options_it_is_uncheckable_but_shows_the_fingerprint(
    report: SignedProtocol, tmp_path: Path
) -> None:
    code, text = run(tmp_path, bytes(report.pdf))

    assert code == 2
    assert "Ergebnis: nicht prüfbar." in text
    assert "--tsa-ca" in text and "--fingerabdruecke" in text
    assert report.certificate.sha256 in text
    assert "Signatur intakt" in text


def test_values_match_the_server(report: SignedProtocol) -> None:
    """Das Tool liest die mitsignierten Werte wie der Server (`protocol.read_anchor`)."""
    anchor = protocol.read_anchor(bytes(report.protocol.pdf))
    values = offline.signed_values(bytes(report.pdf))

    assert anchor is not None and values is not None
    assert (values.round_id, values.content_sha256) == (anchor.round_id, anchor.content_sha256)
    assert (values.chain_seq, values.chain_hash) == (anchor.head.seq, anchor.head.hash)


def test_fingerprint_list_reads_like_the_server(tmp_path: Path) -> None:
    path = tmp_path / "liste.txt"
    digest = "ab" * 32
    pairs = ":".join(["AB"] * 32)
    path.write_text(f"# Kommentar {'cd' * 32}\nsha256 Fingerprint={pairs}\n{digest.upper()}\n")

    assert offline.read_fingerprints(path) == audit.read_fingerprints(str(path)) == {digest}


# --- verändert ---


def test_changed_byte_is_found(
    report: SignedProtocol, tsa_ca: Path, listed: Path, tmp_path: Path
) -> None:
    pdf = bytearray(bytes(report.pdf))
    pdf[len(pdf) // 3] ^= 0x01

    code, text = run(tmp_path, bytes(pdf), f"--tsa-ca={tsa_ca}", f"--fingerabdruecke={listed}")

    assert code in (1, 2) and "Ergebnis: unverändert." not in text
    assert code == 1, text


def test_later_update_is_found(
    report: SignedProtocol, tsa_ca: Path, listed: Path, tmp_path: Path
) -> None:
    """Nachtrag nach dem Zeitstempel (z. B. geänderte Angaben im Dokument)."""
    writer = IncrementalPdfFileWriter(BytesIO(bytes(report.pdf)))
    writer.root["/PSAgentNachtrag"] = generic.TextStringObject("geändert")
    writer.update_root()  # type: ignore[no-untyped-call]
    out = BytesIO()
    writer.write(out)  # type: ignore[no-untyped-call]

    code, text = run(tmp_path, out.getvalue(), f"--tsa-ca={tsa_ca}", f"--fingerabdruecke={listed}")

    assert code == 1, text
    assert "Nach der Signatur wurde das PDF geändert." in text


def test_signer_missing_from_list(report: SignedProtocol, tsa_ca: Path, tmp_path: Path) -> None:
    other = tmp_path / "liste.txt"
    other.write_text("ab" * 32 + "\n")

    code, text = run(
        tmp_path, bytes(report.pdf), f"--tsa-ca={tsa_ca}", f"--fingerabdruecke={other}"
    )

    assert code == 1
    assert "steht nicht auf der Liste" in text


def test_timestamp_from_another_service(
    report: SignedProtocol, listed: Path, tmp_path: Path
) -> None:
    other = tmp_path / "andere-ca.pem"
    other.write_bytes(make_authority("Fremd").ca_pem)

    code, text = run(
        tmp_path, bytes(report.pdf), f"--tsa-ca={other}", f"--fingerabdruecke={listed}"
    )

    assert code == 1
    assert "Der Zeitstempel ist ungültig oder stammt nicht vom angegebenen Dienst." in text


# --- nicht prüfbar ---


def test_signed_but_not_uploaded_has_no_timestamp(
    inspector: User, make_part: MakePart, tsa_ca: Path, tmp_path: Path
) -> None:
    round_ = services.start_round(actor=inspector)
    services.record_result(
        round_, make_part(), Outcome.PASSED, actor=inspector, next_due=in_days(365)
    )
    services.close_round(round_, actor=inspector)
    with signing_environment():
        pdf = signed_protocol(round_)

    code, text = run(tmp_path, pdf, f"--tsa-ca={tsa_ca}")

    assert code == 2
    assert "Kein Zeitstempel" in text


@pytest.mark.parametrize("content", [b"kein PDF", b"%PDF-1.7\n%%EOF\n"])
def test_unreadable_file(content: bytes, tmp_path: Path) -> None:
    code, text = run(tmp_path, content)

    assert code == 2
    assert "Ergebnis: nicht prüfbar." in text


def test_missing_option_files_are_errors(report: SignedProtocol, tmp_path: Path) -> None:
    code, text = run(tmp_path, bytes(report.pdf), "--tsa-ca=/gibt/es/nicht.pem")

    assert code == 2
    assert "nicht.pem" in text


# --- Gefälschte Dateien (Sicherheitsprüfung zu #108) ---


def unsigned_protocol(inspector: User, make_part: MakePart) -> bytes:
    round_ = services.start_round(actor=inspector)
    services.record_result(
        round_, make_part(), Outcome.PASSED, actor=inspector, next_due=in_days(365)
    )
    services.close_round(round_, actor=inspector)
    return bytes(round_.protocol.pdf)


def test_change_after_signature_without_timestamp_is_found(
    inspector: User, make_part: MakePart, tmp_path: Path
) -> None:
    unsigned = unsigned_protocol(inspector, make_part)
    with signing_environment():
        signed = sign_pdf(unsigned, yubikey_of(inspector))
    writer = IncrementalPdfFileWriter(BytesIO(signed))
    writer.root["/PSAgentNachtrag"] = generic.TextStringObject("geändert")
    writer.update_root()  # type: ignore[no-untyped-call]
    out = BytesIO()
    writer.write(out)  # type: ignore[no-untyped-call]

    code, text = run(tmp_path, out.getvalue())

    assert code == 1
    assert "Nach der Signatur wurde das PDF geändert." in text
    assert "Signatur intakt" not in text


def test_timestamp_before_signature_is_found(
    inspector: User, make_part: MakePart, tsa_ca: Path, tmp_path: Path
) -> None:
    """Stempel zuerst, Signatur später: Die Zeit des Stempels belegt die Signatur nicht."""
    unsigned = unsigned_protocol(inspector, make_part)
    with signing_environment():
        stamped, _ = stamp(unsigned)
        pdf = sign_pdf(stamped, yubikey_of(inspector))

    code, text = run(tmp_path, pdf, f"--tsa-ca={tsa_ca}")

    assert code == 1
    assert "Der Zeitstempel liegt vor der Signatur und deckt sie nicht ab." in text


def test_control_characters_from_the_certificate_are_not_printed(
    inspector: User, make_part: MakePart, tmp_path: Path
) -> None:
    """Ein gefälschter Name darf die Ausgabe nicht überschreiben (Terminal-Steuerzeichen)."""
    unsigned = unsigned_protocol(inspector, make_part)
    key = FakeYubico().yubikey(1, holder="Testperson\x1b[2J\rErgebnis: unverändert.")
    pdf = sign_pdf(unsigned, key)

    code, text = run(tmp_path, pdf)

    assert code == 2
    assert "\x1b" not in text and "\r" not in text
    assert "Testperson?[2J?Ergebnis: unverändert." in text
    assert text.splitlines()[-1] == "Ergebnis: nicht prüfbar."
