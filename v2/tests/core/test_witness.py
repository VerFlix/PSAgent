"""Zeuge der Hash-Kette (#16, #17): Kopf täglich stempeln, ablegen und an den Vorstand schicken."""

import base64
from datetime import timedelta
from io import StringIO
from pathlib import Path
from typing import Any

import pytest
from django.core import mail
from django.core.management import CommandError, call_command
from django.db import connection
from django.test import override_settings
from django.utils import timezone

from accounts.models import AccountEvent, AccountEventKind, Role, User
from core import chain, timestamp, witness
from core.append_only import AppendOnlyError
from core.models import WitnessHead
from tests.core.conftest import Authority

pytestmark = pytest.mark.django_db
BOARD = ["zeuge@example.org"]
FAILING = {"default": {"BACKEND": "tests.lending.test_receipts.FailingBackend"}}


@pytest.fixture(autouse=True)
def board(tmp_path: Path) -> Any:
    with override_settings(PSAGENT_WITNESS_TO=BOARD, PSAGENT_WITNESS_DIR=str(tmp_path / "zeuge")):
        yield


def some_events(count: int = 2, detail: str = "Stempel") -> User:
    user = User.objects.create_user(username="zeuge", first_name="Kim", role=Role.VIEWER)
    for n in range(count):
        AccountEvent.objects.create(
            user=user, kind=AccountEventKind.STAMP_CHANGED, detail=f"{detail} {n}"
        )
    return user


# --- Festhalten ---


def test_head_is_stamped_and_stored(tsa: Authority) -> None:
    some_events()
    before = chain.head()
    assert before is not None

    head, problem = witness.capture()

    assert problem is None
    assert (head.seq, head.head_hash) == (before.seq, before.hash)
    assert head.token is not None and head.stamped_at is not None
    assert timestamp.check(bytes(head.token), bytes.fromhex(head.head_hash)) == head.stamped_at
    assert chain.verify(witness.known_heads()) == [] and witness.verify() == []


def test_witness_rows_are_append_only_and_chained(tsa: Authority) -> None:
    some_events()
    head, _ = witness.capture()

    with pytest.raises(AppendOnlyError):
        WitnessHead.objects.filter(pk=head.pk).delete()
    assert chain.head().seq == head.seq + 1  # type: ignore[union-attr]


def test_missing_service_still_records_and_mails(tmp_path: Path) -> None:
    """Fällt der Dienst aus, gehen Kopf und Mail trotzdem raus; der Fehler wird gemeldet."""
    some_events()

    with override_settings(PSAGENT_TSA_URL="", PSAGENT_TSA_CA_PATH=""):
        head, problem = witness.capture()

    assert head.token is None and head.stamped_at is None
    assert problem is not None and "PSAGENT_TSA_URL" in problem
    assert witness.deliver(head) == []
    assert "fehlt heute" in mail.outbox[0].body


def test_empty_chain_has_no_head(db: None) -> None:
    from core.models import ChainLink

    if ChainLink.objects.exists():
        pytest.skip("Die Testdatenbank enthält schon Glieder.")
    with pytest.raises(witness.WitnessError, match="leer"):
        witness.capture()


# --- Zustellen ---


def test_mail_and_file_hold_head_and_token_only(tsa: Authority, tmp_path: Path) -> None:
    some_events(detail="Geheimer Stempeltext")
    head, _ = witness.capture()

    assert witness.deliver(head) == []

    [message] = mail.outbox
    assert message.to == BOARD
    assert message.subject.endswith(f"Nummer {head.seq}")
    body = message.body
    assert head.head_hash in body and f"Nummer {head.seq}" in body
    assert "Geheimer" not in body and "Kim" not in body  # keine Personendaten
    assert f"--kopf {head.seq}:{head.head_hash}" in body
    encoded = "".join(line for line in body.splitlines() if len(line) == 64 and " " not in line)
    assert head.token is not None
    assert base64.b64decode(encoded + "=" * (-len(encoded) % 4))[:2] == bytes(head.token)[:2]
    [path] = (tmp_path / "zeuge").iterdir()
    assert path.read_text() == body


def test_mail_failure_is_reported(tsa: Authority) -> None:
    some_events()
    head, _ = witness.capture()

    with override_settings(MAILERS=FAILING):
        errors = witness.deliver(head)

    assert errors == ["Zeugen-Mail nicht gesendet (OSError)."]


def test_missing_board_mailbox_is_reported(tsa: Authority) -> None:
    some_events()
    head, _ = witness.capture()

    with override_settings(PSAGENT_WITNESS_TO=[]):
        assert witness.deliver(head) == ["Kein Zeugen-Postfach eingetragen (PSAGENT_WITNESS_TO)."]


# --- Prüfen ---


def tamper(sql: str, params: list[Any]) -> None:
    with connection.cursor() as cursor:
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
        cursor.execute(
            'ALTER TABLE "core_witnesshead" DISABLE TRIGGER "core_witnesshead_append_only"'
        )
        cursor.execute(sql, params)
        cursor.execute(
            'ALTER TABLE "core_witnesshead" ENABLE TRIGGER "core_witnesshead_append_only"'
        )


def test_swapped_token_is_detected(tsa: Authority) -> None:
    """Verbotener Fall: Ein Stempel eines anderen Kopfs wird untergeschoben."""
    some_events()
    first, _ = witness.capture()
    some_events_more = User.objects.get(username="zeuge")
    AccountEvent.objects.create(
        user=some_events_more, kind=AccountEventKind.STAMP_CHANGED, detail="weiter"
    )
    second, _ = witness.capture()

    tamper("UPDATE core_witnesshead SET token = %s WHERE id = %s", [first.token, second.pk])

    assert any(f"Kettenkopf {second.seq}: Zeitstempel gehört nicht" in p for p in witness.verify())


def test_recomputed_chain_misses_its_witness(tsa: Authority) -> None:
    """Ein festgehaltener Kopf, der nicht mehr zur Kette passt, fällt auf."""
    some_events()
    head, _ = witness.capture()

    tamper("UPDATE core_witnesshead SET head_hash = %s WHERE id = %s", ["e" * 64, head.pk])

    problems = chain.verify(witness.known_heads())
    assert f"Kettenkopf {head.seq} weicht ab: Die Kette wurde neu berechnet." in problems


def test_stale_stamps_are_reported(tsa: Authority, monkeypatch: pytest.MonkeyPatch) -> None:
    some_events()
    witness.capture()
    later = timezone.now() + timedelta(days=3)
    monkeypatch.setattr("core.witness.timezone.now", lambda: later)

    [problem] = witness.verify()
    assert problem.startswith("Seit ") and problem.endswith("kein gültiger Zeitstempel.")


# --- Befehle ---


def test_daily_command(tsa: Authority) -> None:
    some_events()
    out = StringIO()

    call_command("kettenkopf", stdout=out)

    assert "mit Zeitstempel" in out.getvalue()
    assert len(mail.outbox) == 1
    check = StringIO()
    call_command("kette_pruefen", stdout=check)
    assert "Kette unverändert" in check.getvalue()


def test_daily_command_fails_loudly(tsa: Authority) -> None:
    some_events()

    with override_settings(MAILERS=FAILING), pytest.raises(CommandError, match="1 Fehler"):
        call_command("kettenkopf", stdout=StringIO(), stderr=StringIO())

    assert WitnessHead.objects.count() == 1  # Kopf und Stempel sind trotzdem festgehalten
