"""Manipulations-Katalog (#19, Abnahme Phase 3): jede gezielte Manipulation wird erkannt.

Der Katalog steht in `docs/manipulationen.md`; jede Zeile nennt den Test, der sie abdeckt. Hier
stehen die Fälle, die das Prüfwerkzeug `kette_pruefen` als Ganzes finden muss, vor allem die
eines Angreifers mit vollem Datenbankzugang, der Spalten und Kette passend neu berechnet. Für die
zwei Fälle, die nur der Zeuge aufdeckt, prüft je ein Test auch die Grenze ohne Zeugen-Datei.
"""

import hashlib
import re
from io import StringIO
from pathlib import Path

import pytest
from django.core.management import CommandError, call_command
from django.db import connection

from accounts.models import AccountEvent, AccountEventKind, Role, User
from core import chain, timestamp, witness
from core.models import ChainLink, WitnessHead
from inspections import services
from inspections.models import (
    InspectionRound,
    Outcome,
    RoundEvent,
    RoundEventKind,
    RoundProtocol,
    SignedProtocol,
)
from tests.helpers import tamper
from tests.inspections.conftest import MakePart, in_days
from tests.signing import sign, signing_environment

pytestmark = pytest.mark.django_db


def check(*args: str) -> tuple[int, str]:
    """Rückgabewert und alle Meldungen von `kette_pruefen`."""
    out, err = StringIO(), StringIO()
    with signing_environment():
        try:
            call_command("kette_pruefen", *args, stdout=out, stderr=err)
        except CommandError as error:
            return error.returncode, out.getvalue() + err.getvalue()
    return 0, out.getvalue() + err.getvalue()


def signed_round(inspector: User, make_part: MakePart, ei: str = "TST-M1") -> InspectionRound:
    round_ = services.start_round(actor=inspector)
    services.record_result(
        round_, make_part(ei=ei), Outcome.PASSED, actor=inspector, next_due=in_days(365)
    )
    services.close_round(round_, actor=inspector)
    sign(round_, actor=inspector)
    return round_


def events(count: int) -> list[AccountEvent]:
    user = User.objects.create_user(username="katalog", first_name="Testperson", role=Role.VIEWER)
    return [
        AccountEvent.objects.create(user=user, kind=AccountEventKind.STAMP_CHANGED, detail=f"{n}")
        for n in range(count)
    ]


def board_file(path: Path) -> Path:
    """Zeugen-Datei für den aktuellen Kopf, die nur beim Vorstand liegt (nicht in der Datenbank)."""
    current = chain.head()
    assert current is not None
    with signing_environment():
        token = timestamp.request(bytes.fromhex(current.hash))
        stamped_at = timestamp.check(token, bytes.fromhex(current.hash))
    path.write_text(
        witness.report(
            WitnessHead(seq=current.seq, head_hash=current.hash, token=token, stamped_at=stamped_at)
        )
    )
    return path


def recompute_chain() -> None:
    """Wie ein Angreifer mit Datenbankzugang: alle Glieder passend zu den Zeilen neu berechnen."""
    models = {model._meta.label: model for model in chain.chained_models()}
    prev = chain.GENESIS
    for entry in ChainLink.objects.order_by("seq"):
        fields = list(entry.fields)
        row = models[entry.table]._base_manager.filter(pk=entry.row_pk).values(*fields).first()
        assert row is not None
        payload = chain.payload_digest(entry.table, entry.row_pk, row)
        digest = chain.link_digest(
            seq=entry.seq,
            prev=prev,
            table=entry.table,
            pk=entry.row_pk,
            fields=fields,
            payload=payload,
            at=entry.at,
        )
        tamper(
            "core_chainlink",
            "UPDATE core_chainlink SET prev_hash = %s, payload_sha256 = %s, hash = %s"
            " WHERE seq = %s",
            [prev, payload, digest, entry.seq],
        )
        prev = digest
    assert chain.verify() == []  # die Kette allein passt wieder


# --- M1–M3: Datenbank an der App vorbei geändert ---


def test_m1_changed_field_is_found(inspector: User, make_part: MakePart) -> None:
    round_ = signed_round(inspector, make_part)
    result = round_.results.get()

    tamper(
        "inspections_inspectionresult",
        "UPDATE inspections_inspectionresult SET next_due = next_due + 365 WHERE id = %s",
        [result.pk],
    )

    code, text = check()
    assert code == 1
    assert f"(inspections.InspectionResult #{result.pk}): Vorgang wurde geändert." in text
    assert "Die Ergebnisse der Runde passen nicht mehr zum Abschluss." in text


def test_m2_deleted_entry_is_found() -> None:
    _, gone, _ = events(3)

    tamper("accounts_accountevent", "DELETE FROM accounts_accountevent WHERE id = %s", [gone.pk])

    code, text = check()
    assert code == 1
    assert f"(accounts.AccountEvent #{gone.pk}): Vorgang wurde gelöscht." in text


def test_m3_inserted_entry_is_found() -> None:
    [first] = events(1)
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO accounts_accountevent"
            " (user_id, kind, old_role, new_role, reason, detail, at)"
            " VALUES (%s, 'stamp_changed', '', '', '', 'untergeschoben', now()) RETURNING id",
            [first.user_id],
        )
        row = cursor.fetchone()
    assert row is not None

    code, text = check()
    assert code == 1
    assert f"accounts.AccountEvent #{row[0]}: Vorgang ohne Kettenglied" in text


# --- M4–M6: Angreifer berechnet die Kette passend neu ---


def test_m4_result_changed_and_everything_recomputed(inspector: User, make_part: MakePart) -> None:
    """Ergebnis geändert, Prüfsummen und Kette neu berechnet: Das Signierte widerspricht."""
    round_ = signed_round(inspector, make_part)
    result = round_.results.get()
    tamper(
        "inspections_inspectionresult",
        "UPDATE inspections_inspectionresult SET comment = %s WHERE id = %s",
        ["nachträglich geschönt", result.pk],
    )
    digest = services.content_sha256(round_)
    closed = RoundEvent.objects.get(round=round_, kind=RoundEventKind.CLOSED)
    tamper(
        "inspections_roundevent",
        "UPDATE inspections_roundevent SET content_sha256 = %s WHERE id = %s",
        [digest, closed.pk],
    )
    protocol = RoundProtocol.objects.get(round=round_)
    tamper(
        "inspections_roundprotocol",
        "UPDATE inspections_roundprotocol SET content_sha256 = %s WHERE id = %s",
        [digest, protocol.pk],
    )
    recompute_chain()
    new_head = ChainLink.objects.get(seq=protocol.chain_seq).hash
    tamper(
        "inspections_roundprotocol",
        "UPDATE inspections_roundprotocol SET chain_hash = %s WHERE id = %s",
        [new_head, protocol.pk],
    )
    recompute_chain()

    code, text = check()
    assert code == 1
    assert "Die gespeicherten Werte des Protokolls weichen vom signierten ab." in text
    assert "Die Ergebnisse der Runde passen nicht mehr zum Abschluss." in text
    assert f"Kettenkopf {protocol.chain_seq} weicht ab: Die Kette wurde neu berechnet." in text


def test_m5_recomputed_chain_needs_the_witness(tmp_path: Path) -> None:
    changed, *_ = events(3)
    mail = board_file(tmp_path / "zeuge.txt")
    tamper(
        "accounts_accountevent",
        "UPDATE accounts_accountevent SET detail = 'geändert' WHERE id = %s",
        [changed.pk],
    )
    recompute_chain()

    assert check()[0] == 0  # Grenze: ohne Zeugen sieht die Datenbank stimmig aus
    code, text = check(f"--zeugen={mail}")
    assert code == 1
    assert "weicht ab: Die Kette wurde neu berechnet." in text


def test_m6_restored_older_backup_needs_the_witness(tmp_path: Path) -> None:
    """Datenbank auf einen älteren Stand zurückgesetzt (Sicherung von gestern eingespielt)."""
    *_, last = events(3)
    mail = board_file(tmp_path / "zeuge.txt")
    head = chain.head()
    assert head is not None
    tamper("core_chainlink", "DELETE FROM core_chainlink WHERE seq = %s", [head.seq])
    tamper("accounts_accountevent", "DELETE FROM accounts_accountevent WHERE id = %s", [last.pk])

    assert check()[0] == 0  # Grenze: ohne Zeugen sieht die Datenbank stimmig aus
    code, text = check(f"--zeugen={mail}")
    assert code == 1
    assert f"Kettenkopf {head.seq} fehlt: Die Kette wurde gekürzt." in text


# --- M7–M8: Prüfberichte ---


SWAP = {
    SignedProtocol: "UPDATE inspections_signedprotocol SET pdf = %s, sha256 = %s WHERE id = %s",
    RoundProtocol: "UPDATE inspections_roundprotocol SET pdf = %s, sha256 = %s WHERE id = %s",
}


def swap(model: type[SignedProtocol | RoundProtocol], first: int, second: int) -> None:
    """Tauscht `pdf` und `sha256` zweier Zeilen (eindeutige Prüfsumme: über einen Platzhalter)."""
    rows = {
        pk: (bytes(pdf), sha) for pk, pdf, sha in model.objects.values_list("pk", "pdf", "sha256")
    }
    table, update = model._meta.db_table, SWAP[model]
    tamper(table, update, [b"", "0" * 64, second])
    tamper(table, update, [*rows[second], first])
    tamper(table, update, [*rows[first], second])


def test_m7_swapped_reports_are_found(inspector: User, make_part: MakePart) -> None:
    """Signierte Protokolle zweier Runden samt Original vertauscht."""
    first = signed_round(inspector, make_part, "TST-M7A")
    second = signed_round(inspector, make_part, "TST-M7B")
    a, b = SignedProtocol.objects.get(round=first), SignedProtocol.objects.get(round=second)
    swap(SignedProtocol, a.pk, b.pk)
    swap(RoundProtocol, a.protocol_id, b.protocol_id)

    code, text = check()
    assert code == 1
    assert f"Prüfbericht {first}: Das signierte Protokoll gehört zu Runde {second.pk}." in text
    assert f"Prüfbericht {second}: Das signierte Protokoll gehört zu Runde {first.pk}." in text


def test_m8_unsigned_change_after_signing_is_found(inspector: User, make_part: MakePart) -> None:
    """Nachtrag im signierten PDF (z. B. eine Seite), Prüfsumme in der Datenbank angepasst."""
    round_ = signed_round(inspector, make_part)
    entry = SignedProtocol.objects.get(round=round_)
    pdf = bytes(entry.pdf) + b"\n% Nachtrag\n"
    tamper(
        "inspections_signedprotocol",
        "UPDATE inspections_signedprotocol SET pdf = %s, sha256 = %s WHERE id = %s",
        [pdf, hashlib.sha256(pdf).hexdigest(), entry.pk],
    )

    code, text = check()
    assert code == 1
    assert f"(inspections.SignedProtocol #{entry.pk}): Vorgang wurde geändert." in text


def test_catalog_names_existing_tests() -> None:
    """Jeder Test, den `docs/manipulationen.md` nennt, gibt es auch."""
    root = Path(__file__).resolve().parents[2]
    catalog = (root / "docs" / "manipulationen.md").read_text()
    named = set(re.findall(r"\b(test_\w+)`", catalog))
    defined = {
        name
        for path in (root / "tests").rglob("test_*.py")
        for name in re.findall(r"^def (test_\w+)", path.read_text(), re.MULTILINE)
    }

    assert len(named) >= 20
    assert named <= defined, sorted(named - defined)
