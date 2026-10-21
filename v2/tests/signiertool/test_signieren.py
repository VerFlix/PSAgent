"""Signier-Tool für den PC (#38 Schritt 3, Parität 8).

Festlegungen (Felix, 21.10.2026): Kommandozeile unter Windows; Unterschrift einmal per Befehl als
Bild hinterlegen; Installation mit uv aus dem Repo, Hochladen im Browser.

Getestet wird ohne Hardware: mit einem Software-Schlüssel und mit SoftHSM über PKCS#11, wie mit
dem YubiKey. Entscheidend ist jeweils, dass der Server das signierte PDF annimmt.
"""

import os
import shutil
import subprocess
from io import BytesIO
from pathlib import Path

import pytest
from asn1crypto import keys as asn1_keys
from asn1crypto import x509 as asn1_x509
from cryptography.hazmat.primitives import serialization
from PIL import Image
from psagent_signieren import cli, core
from pyhanko.pdf_utils.reader import PdfFileReader
from pyhanko.sign import signers
from pyhanko_certvalidator.registry import SimpleCertificateStore

from accounts.models import User
from inspections import services
from inspections.models import InspectionRound, Outcome, RoundProtocol, RoundState
from tests.accounts.yubikey import FakeYubiKey
from tests.inspections.conftest import MakePart, in_days
from tests.signing import sign, signing_environment, yubikey_of

pytestmark = pytest.mark.django_db
SOFTHSM = "/usr/lib/softhsm/libsofthsm2.so"


def closed(inspector: User, make_part: MakePart, parts: int = 2) -> InspectionRound:
    round_ = services.start_round(actor=inspector)
    for _ in range(parts):
        services.record_result(
            round_, make_part(), Outcome.PASSED, actor=inspector, next_due=in_days(365)
        )
    services.close_round(round_, actor=inspector)
    return round_


def protocol_of(round_: InspectionRound) -> bytes:
    return bytes(RoundProtocol.objects.get(round=round_).pdf)


def software_signer(key: FakeYubiKey) -> signers.SimpleSigner:
    der = serialization.Encoding.DER
    cert = asn1_x509.Certificate.load(key.certificate.public_bytes(der))
    private = asn1_keys.PrivateKeyInfo.load(
        key.key.private_bytes(der, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    )
    return signers.SimpleSigner(
        signing_cert=cert,
        signing_key=private,
        cert_registry=SimpleCertificateStore.from_certs([cert]),  # type: ignore[no-untyped-call]
    )


@pytest.fixture
def signature_png(tmp_path: Path) -> Path:
    path = tmp_path / "unterschrift.png"
    Image.new("RGBA", (300, 100), (0, 0, 120, 255)).save(path)
    return path


# --- Zusammenfassen und prüfen vor dem Signieren ---


def test_summary_shows_round_parts_and_chain_head(inspector: User, make_part: MakePart) -> None:
    round_ = closed(inspector, make_part, parts=3)
    entry = RoundProtocol.objects.get(round=round_)

    summary = core.summarize(protocol_of(round_))

    assert summary.round == str(round_.pk)
    assert summary.parts == "3"
    assert summary.chain_head == str(entry.chain_seq)
    assert summary.sha256 == entry.sha256


def test_only_unsigned_protocols_are_signed(inspector: User, make_part: MakePart) -> None:
    round_ = closed(inspector, make_part)
    with signing_environment():
        key = yubikey_of(inspector)
    signed = core.sign(protocol_of(round_), software_signer(key))

    with pytest.raises(core.SigningError, match="bereits signiert"):
        core.summarize(signed)
    with pytest.raises(core.SigningError, match="lesbares PDF"):
        core.summarize(b"kein PDF")


def test_existing_output_is_never_overwritten(tmp_path: Path) -> None:
    source = tmp_path / "protokoll.pdf"
    (tmp_path / "protokoll_signiert.pdf").write_bytes(b"alt")

    with pytest.raises(core.SigningError, match="gibt es schon"):
        core.output_path(source)


# --- Der Server nimmt die Signatur des Tools an ---


def test_parity_8_server_accepts_tool_signature_with_image(
    inspector: User, make_part: MakePart, signature_png: Path
) -> None:
    round_ = closed(inspector, make_part)
    with signing_environment():
        key = yubikey_of(inspector)

    signed = core.sign(protocol_of(round_), software_signer(key), image=signature_png)
    sign(round_, actor=inspector, pdf=signed)

    assert round_.state() == RoundState.SIGNED
    reader = PdfFileReader(BytesIO(signed))
    field = reader.embedded_signatures[0].sig_field
    appearance = field["/AP"]["/N"].get_object()
    assert "/XObject" in appearance["/Resources"]  # Bild der Unterschrift im Feld


def test_server_accepts_tool_signature_without_image(inspector: User, make_part: MakePart) -> None:
    round_ = closed(inspector, make_part)
    with signing_environment():
        key = yubikey_of(inspector)

    sign(round_, actor=inspector, pdf=core.sign(protocol_of(round_), software_signer(key)))

    assert round_.state() == RoundState.SIGNED


# --- Unterschrift hinterlegen ---


def test_signature_image_is_stored_once(
    tmp_path: Path, signature_png: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = tmp_path / "einstellungen"
    monkeypatch.setenv("PSAGENT_SIGNIEREN_ORDNER", str(folder))

    assert cli.main(["unterschrift", str(signature_png)]) == 0

    assert cli.signature_image() == folder / "unterschrift.png"
    with Image.open(folder / "unterschrift.png") as stored:
        assert stored.format == "PNG"


def test_invalid_image_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PSAGENT_SIGNIEREN_ORDNER", str(tmp_path / "einstellungen"))
    bad = tmp_path / "kein-bild.png"
    bad.write_bytes(b"kein Bild")

    assert cli.main(["unterschrift", str(bad)]) == 1
    assert cli.signature_image() is None


# --- Kommandozeile mit PKCS#11 (SoftHSM statt YubiKey) ---


def _pkcs11_tool(config: Path, *args: str) -> None:
    tool = shutil.which("pkcs11-tool")
    assert tool is not None, "pkcs11-tool fehlt (Paket opensc)"
    subprocess.run(  # noqa: S603 – fester Befehl, Testdaten
        [tool, "--module", SOFTHSM, "--login", "--pin", "123456", *args],
        check=True,
        capture_output=True,
        env={**os.environ, "SOFTHSM2_CONF": str(config)},
    )


@pytest.fixture
def softhsm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Ein Token wie der YubiKey: Schlüssel und Zertifikat mit den Beschriftungen von Slot 9c."""
    assert Path(SOFTHSM).exists(), "SoftHSM fehlt (Paket softhsm2)"
    folder = tmp_path / "hsm"
    (folder / "tokens").mkdir(parents=True)
    config = folder / "softhsm2.conf"
    config.write_text(
        f"directories.tokendir = {folder / 'tokens'}\nobjectstore.backend = file\n"
        "log.level = ERROR\n"
    )
    util = shutil.which("softhsm2-util")
    assert util is not None, "softhsm2-util fehlt (Paket softhsm2)"
    subprocess.run(  # noqa: S603 – fester Befehl, Testdaten
        [
            util,
            "--init-token",
            "--free",
            "--label",
            "YubiKey PIV (Testdaten)",
            "--pin",
            "123456",
            "--so-pin",
            "12345678",
        ],
        check=True,
        capture_output=True,
        env={**os.environ, "SOFTHSM2_CONF": str(config)},
    )
    monkeypatch.setenv("SOFTHSM2_CONF", str(config))
    return config


def test_parity_8_command_line_signs_with_pkcs11_token(
    inspector: User,
    make_part: MakePart,
    softhsm: Path,
    tmp_path: Path,
    signature_png: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    round_ = closed(inspector, make_part)
    with signing_environment():
        key = yubikey_of(inspector)
    der = serialization.Encoding.DER
    (tmp_path / "key.der").write_bytes(
        key.key.private_bytes(der, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    )
    (tmp_path / "cert.der").write_bytes(key.certificate.public_bytes(der))
    for kind, label, path in [
        ("privkey", "Private key for Digital Signature", "key.der"),
        ("cert", "X.509 Certificate for Digital Signature", "cert.der"),
    ]:
        _pkcs11_tool(
            softhsm,
            "--write-object",
            str(tmp_path / path),
            "--type",
            kind,
            "--label",
            label,
            "--id",
            "02",
        )
    source = tmp_path / f"Pruefprotokoll_Runde_{round_.pk}.pdf"
    source.write_bytes(protocol_of(round_))
    monkeypatch.setenv("PSAGENT_SIGNIEREN_ORDNER", str(tmp_path / "einstellungen"))
    cli.store_image(signature_png)
    monkeypatch.setattr("getpass.getpass", lambda prompt: "123456")

    code = cli.main([str(source), "--modul", SOFTHSM, "--ja", "--ohne-anzeige"])

    target = tmp_path / f"Pruefprotokoll_Runde_{round_.pk}_signiert.pdf"
    assert code == 0 and target.exists()
    sign(round_, actor=inspector, pdf=target.read_bytes())
    assert round_.state() == RoundState.SIGNED


def test_wrong_pin_is_reported(
    inspector: User,
    make_part: MakePart,
    softhsm: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    round_ = closed(inspector, make_part)
    source = tmp_path / "protokoll.pdf"
    source.write_bytes(protocol_of(round_))
    monkeypatch.setenv("PSAGENT_SIGNIEREN_ORDNER", str(tmp_path / "einstellungen"))
    monkeypatch.setattr("getpass.getpass", lambda prompt: "000000")

    assert cli.main([str(source), "--modul", SOFTHSM, "--ja", "--ohne-anzeige"]) == 1
    assert not (tmp_path / "protokoll_signiert.pdf").exists()


def test_declining_signs_nothing(
    inspector: User, make_part: MakePart, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    round_ = closed(inspector, make_part)
    source = tmp_path / "protokoll.pdf"
    source.write_bytes(protocol_of(round_))
    monkeypatch.setenv("PSAGENT_SIGNIEREN_ORDNER", str(tmp_path / "einstellungen"))
    monkeypatch.setattr("builtins.input", lambda prompt: "n")

    assert cli.main([str(source), "--ohne-anzeige"]) == 1
    assert not (tmp_path / "protokoll_signiert.pdf").exists()
