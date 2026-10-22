"""Prüfwerkzeug `kette_pruefen` (#19): Kette, Zeugen-Dateien, Prüfberichte, Fingerabdrücke.

Festlegungen (Felix, 22.10.2026): Zeugen-Mails und Kopf-Dateien liest der Befehl selbst
(`--zeugen`); die Liste der Fingerabdrücke kommt als Datei von außen (`--fingerabdruecke`), nicht
aus dem Repo. Ergebnis unverändert (0), verändert (1) oder nicht prüfbar (2).
"""

from io import StringIO
from pathlib import Path

import pytest
from django.core import mail
from django.core.management import CommandError, call_command
from django.test import override_settings
from django.utils import timezone

from accounts.models import User
from core import audit, chain, timestamp, witness
from core.models import WitnessHead
from inspections import services
from inspections.models import InspectionRound, Outcome, SignedProtocol
from legacy.models import LegacyReport
from tests.helpers import tamper
from tests.inspections.conftest import MakePart, in_days
from tests.signing import sign, signing_environment

pytestmark = pytest.mark.django_db


def signed_round(inspector: User, make_part: MakePart) -> InspectionRound:
    round_ = services.start_round(actor=inspector)
    services.record_result(
        round_, make_part(), Outcome.PASSED, actor=inspector, next_due=in_days(365)
    )
    services.close_round(round_, actor=inspector)
    sign(round_, actor=inspector)
    return round_


def check(*args: str) -> tuple[int, str, str]:
    """Führt den Befehl in der Signier-Umgebung aus; gibt Rückgabewert, Ausgabe und Fehler."""
    out, err = StringIO(), StringIO()
    with signing_environment():
        try:
            call_command("kette_pruefen", *args, stdout=out, stderr=err)
        except CommandError as error:
            return error.returncode, out.getvalue(), err.getvalue() + str(error)
    return 0, out.getvalue(), err.getvalue()


def board_mail(folder: Path) -> tuple[Path, Path]:
    """Hält den Kopf fest wie `manage.py kettenkopf`; gibt Kopf-Datei und gespeicherte Mail."""
    with (
        signing_environment(),
        override_settings(PSAGENT_WITNESS_DIR=str(folder), PSAGENT_WITNESS_TO=["z@example.org"]),
    ):
        head, problem = witness.capture()
        assert problem is None
        assert witness.deliver(head) == []
    [text_file] = folder.glob("kettenkopf-*.txt")
    eml = folder / "zeugen-mail.eml"
    eml.write_bytes(mail.outbox[-1].message().as_bytes())
    return text_file, eml


def file_only_head(path: Path) -> Path:
    """Zeugen-Datei für den aktuellen Kopf, ohne Eintrag in der Datenbank (nur beim Vorstand)."""
    current = chain.head()
    assert current is not None
    with signing_environment():
        token = timestamp.request(bytes.fromhex(current.hash))
        stamped_at = timestamp.check(token, bytes.fromhex(current.hash))
    entry = WitnessHead(seq=current.seq, head_hash=current.hash, token=token, stamped_at=stamped_at)
    path.write_text(witness.report(entry))
    return path


# --- unverändert ---


def test_everything_unchanged(inspector: User, make_part: MakePart, tmp_path: Path) -> None:
    signed_round(inspector, make_part)
    board_mail(tmp_path / "zeuge")
    cert = SignedProtocol.objects.get().certificate.sha256
    listed = tmp_path / "liste.txt"
    pairs = ":".join(cert[n : n + 2].upper() for n in range(0, 64, 2))
    listed.write_text(f"# Papierliste Vorstand\nsha256 Fingerprint={pairs}  # YubiKey 1\n")

    code, out, err = check(f"--zeugen={tmp_path / 'zeuge'}", f"--fingerabdruecke={listed}")

    assert code == 0, err
    assert "Ergebnis: unverändert." in out
    assert "Kette unverändert" in out
    assert "Zeugen-Dateien: 2 Kettenköpfe gelesen, 2 mit gültigem Zeitstempel" in out
    assert "Prüfberichte: 1 von 1 vollständig unverändert" in out
    assert "Alle 1 Signaturzertifikate stehen auf der Liste" in out


def test_mail_and_file_give_the_same_head(tmp_path: Path, inspector: User) -> None:
    text_file, eml = board_mail(tmp_path)

    from_file = audit.parse_witness(audit.witness_text(text_file), "Zeuge")
    from_mail = audit.parse_witness(audit.witness_text(eml), "Zeuge")

    head = WitnessHead.objects.get()
    assert from_file is not None and from_file.head == chain.Head(head.seq, head.head_hash)
    assert from_file.token == bytes(head.token or b"") and from_file.stamped_at is not None
    assert from_mail == from_file


# --- verändert ---


def test_forged_hash_in_witness_file(tmp_path: Path, inspector: User) -> None:
    text_file, _ = board_mail(tmp_path)
    head = WitnessHead.objects.get()
    text_file.write_text(text_file.read_text().replace(head.head_hash, "d" * 64))

    code, _, err = check(f"--zeugen={text_file}")

    assert code == 1
    assert f"Kettenkopf {head.seq} weicht ab" in err
    assert f"Zeugen-Datei {text_file.name} (Kettenkopf {head.seq})" in err


def test_wrong_time_in_witness_file(tmp_path: Path, inspector: User) -> None:
    text_file, _ = board_mail(tmp_path)
    stamped = WitnessHead.objects.get().stamped_at
    assert stamped is not None
    original = f"{stamped:%d.%m.%Y %H:%M:%S} UTC"
    text_file.write_text(text_file.read_text().replace(original, "01.01.2026 08:00:00 UTC"))

    code, _, err = check(f"--zeugen={text_file}")

    assert code == 1
    assert "Die angegebene Zeit weicht vom Zeitstempel ab." in err


def test_changed_report_is_found(inspector: User, make_part: MakePart) -> None:
    round_ = signed_round(inspector, make_part)
    entry = SignedProtocol.objects.get(round=round_)
    pdf = bytearray(bytes(entry.pdf))
    pdf[-50] = (pdf[-50] + 1) % 256
    tamper(
        "inspections_signedprotocol",
        "UPDATE inspections_signedprotocol SET pdf = %s WHERE id = %s",
        [bytes(pdf), entry.pk],
    )

    code, _, err = check()

    assert code == 1
    assert f"Prüfbericht {round_}: Das signierte PDF wurde in der Datenbank verändert." in err


def test_certificate_missing_from_list(
    inspector: User, make_part: MakePart, tmp_path: Path
) -> None:
    signed_round(inspector, make_part)
    entry = SignedProtocol.objects.get().certificate
    listed = tmp_path / "liste.txt"
    listed.write_text("a" * 64 + "\n")

    code, out, err = check(f"--fingerabdruecke={listed}")

    assert code == 1
    assert f"„{entry.name}“ ({entry.sha256}) steht nicht auf der Liste" in err
    assert "1 Fingerabdruck/-drücke der Liste fehlen in PSAgent." in out


# --- nicht prüfbar ---


def test_file_without_head_is_uncheckable(tmp_path: Path) -> None:
    (tmp_path / "notiz.txt").write_text("Bitte aufbewahren.")

    code, _, err = check(f"--zeugen={tmp_path}")

    assert code == 2
    assert "Zeugen-Datei notiz.txt: kein Kettenkopf gefunden." in err


def test_reports_without_yubico_root_are_uncheckable(inspector: User, make_part: MakePart) -> None:
    signed_round(inspector, make_part)

    out, err = StringIO(), StringIO()
    with (
        signing_environment(),
        override_settings(PSAGENT_YUBICO_CA_PATH=""),
        pytest.raises(CommandError) as raised,
    ):
        call_command("kette_pruefen", stdout=out, stderr=err)

    assert raised.value.returncode == 2
    assert "Prüfberichte: Zertifikat: Wurzel von Yubico nicht eingerichtet." in err.getvalue()
    assert "Ergebnis: nicht prüfbar." in out.getvalue()


def test_witness_stamps_without_tsa_root_are_uncheckable(tmp_path: Path, inspector: User) -> None:
    board_mail(tmp_path)

    with (
        override_settings(PSAGENT_TSA_CA_PATH="", PSAGENT_TSA_URL=""),
        pytest.raises(CommandError) as raised,
    ):
        call_command("kette_pruefen", stdout=StringIO(), stderr=StringIO())

    assert raised.value.returncode == 2


# --- Eingaben ---


@pytest.mark.parametrize(
    ("option", "message"),
    [
        ("--zeugen=/gibt/es/nicht.eml", "gibt es nicht"),
        ("--fingerabdruecke=/gibt/es/nicht.txt", "lässt sich nicht lesen"),
    ],
)
def test_missing_files_are_errors(option: str, message: str) -> None:
    with pytest.raises(CommandError, match=message):
        call_command("kette_pruefen", option, stdout=StringIO(), stderr=StringIO())


def test_list_without_fingerprint_is_an_error(tmp_path: Path) -> None:
    listed = tmp_path / "liste.txt"
    listed.write_text("# noch leer\n")

    with pytest.raises(CommandError, match="kein SHA-256-Fingerabdruck"):
        call_command("kette_pruefen", f"--fingerabdruecke={listed}", stdout=StringIO())


def test_empty_folder_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(CommandError, match=r"keine \.eml- oder \.txt-Dateien"):
        call_command("kette_pruefen", f"--zeugen={tmp_path}", stdout=StringIO())


def test_legacy_reports_are_noted() -> None:
    LegacyReport.objects.create(
        legacy_id=1, created_at_raw="01.03.2025", created_at=timezone.now(), confirmed=True
    )

    code, out, _ = check()

    assert code == 0
    assert "1 Altberichte aus dem Altsystem sind ungesichert, ohne Prüfung." in out
