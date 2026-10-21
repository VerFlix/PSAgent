"""Hash-Kette über alle Vorgänge (#15): Ändern, Löschen, Einfügen und Vertauschen fallen auf.

Manipuliert wird wie ein Angreifer mit Datenbankzugang: per SQL, mit ausgeschaltetem Trigger.
"""

import threading
from io import StringIO
from itertools import pairwise

import pytest
from django.core.management import CommandError, call_command
from django.db import connection, connections, transaction

from accounts.models import AccountEvent, AccountEventKind, Role, User
from core import chain
from core.append_only import AppendOnlyError, AppendOnlyModel
from core.chain import GENESIS, Head
from core.models import ChainLink
from emergency.models import Binder, FormKind, FormSheet
from tests.helpers import tamper

pytestmark = pytest.mark.django_db


def make_user(name: str = "kette") -> User:
    return User.objects.create_user(username=name, first_name="Testperson", role=Role.VIEWER)


def event(user: User, detail: str) -> AccountEvent:
    return AccountEvent.objects.create(
        user=user, kind=AccountEventKind.STAMP_CHANGED, detail=detail
    )


def links() -> list[ChainLink]:
    return list(ChainLink.objects.order_by("seq"))


# --- Aufbau ---


def test_every_new_row_gets_a_link() -> None:
    user = make_user()
    first = event(user, "eins")
    second = event(user, "zwei")

    chained = [(link.table, link.row_pk) for link in links()]
    assert ("accounts.AccountEvent", str(first.pk)) in chained
    assert chained.index(("accounts.AccountEvent", str(second.pk))) == len(chained) - 1
    assert [link.seq for link in links()] == list(range(1, len(chained) + 1))
    assert chain.verify() == []


def test_links_point_to_their_predecessor() -> None:
    user = make_user()
    for n in range(3):
        event(user, f"Vorgang {n}")

    chained = links()
    assert chained[0].prev_hash == GENESIS
    for before, after in pairwise(chained):
        assert after.prev_hash == before.hash
    assert chain.head() == Head(chained[-1].seq, chained[-1].hash)


def test_bulk_create_is_chained() -> None:
    binder = Binder.objects.create(sha256="0" * 64)
    sheets = FormSheet.objects.bulk_create(
        FormSheet(binder=binder, kind=FormKind.LOAN, number=n) for n in (1, 2, 3)
    )

    chained = {(link.table, link.row_pk) for link in links()}
    assert {("emergency.FormSheet", str(sheet.pk)) for sheet in sheets} <= chained
    assert chain.verify() == []


def test_rolled_back_rows_leave_no_gap() -> None:
    user = make_user()
    with pytest.raises(RuntimeError), transaction.atomic():
        event(user, "verworfen")
        raise RuntimeError
    event(user, "bleibt")

    assert [link.seq for link in links()] == list(range(1, len(links()) + 1))
    assert chain.verify() == []


def test_every_append_only_table_is_chained() -> None:
    """Eine neue Tabelle, die nur angehängt wird, kann nicht unverkettet bleiben."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT event_object_table FROM information_schema.triggers "
            "WHERE trigger_name = event_object_table || '_append_only'"
        )
        protected = {row[0] for row in cursor.fetchall()} - {"core_chainlink"}

    assert {model._meta.db_table for model in chain.chained_models()} == protected
    assert all(issubclass(model, AppendOnlyModel) for model in chain.chained_models())


def test_links_are_append_only() -> None:
    event(make_user(), "x")

    with pytest.raises(AppendOnlyError):
        ChainLink.objects.all().delete()


def test_link_hash_follows_the_fixed_format() -> None:
    record = event(make_user(), "Format")
    link = ChainLink.objects.get(table="accounts.AccountEvent", row_pk=str(record.pk))

    assert link.hash == chain.link_digest(
        seq=link.seq,
        prev=link.prev_hash,
        table=link.table,
        pk=link.row_pk,
        fields=link.fields,
        payload=link.payload_sha256,
        at=link.at,
    )
    assert link.fields == sorted(f.attname for f in AccountEvent._meta.concrete_fields)


# --- Manipulation ---


def test_changed_row_is_detected() -> None:
    record = event(make_user(), "Original")

    tamper(
        "accounts_accountevent",
        "UPDATE accounts_accountevent SET detail = 'Fälschung' WHERE id = %s",
        [record.pk],
    )

    assert chain.verify() == [
        f"Glied {link.seq} (accounts.AccountEvent #{record.pk}): Vorgang wurde geändert."
        for link in ChainLink.objects.filter(row_pk=str(record.pk), table="accounts.AccountEvent")
    ]


def test_deleted_row_is_detected() -> None:
    record = event(make_user(), "weg")

    tamper("accounts_accountevent", "DELETE FROM accounts_accountevent WHERE id = %s", [record.pk])

    [problem] = chain.verify()
    assert problem.endswith(f"(accounts.AccountEvent #{record.pk}): Vorgang wurde gelöscht.")


def test_inserted_row_is_detected() -> None:
    user = make_user()
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO accounts_accountevent"
            " (user_id, kind, old_role, new_role, reason, detail, at)"
            " VALUES (%s, 'stamp_changed', '', '', '', 'untergeschoben', now()) RETURNING id",
            [user.pk],
        )
        row = cursor.fetchone()
    assert row is not None
    pk = row[0]

    assert chain.verify() == [
        f"accounts.AccountEvent #{pk}: Vorgang ohne Kettenglied (an der App vorbei eingefügt)."
    ]


def test_swapped_links_are_detected() -> None:
    user = make_user()
    event(user, "erster")
    event(user, "zweiter")
    a, b = links()[-2:]

    tamper(
        "core_chainlink",
        "UPDATE core_chainlink SET seq = CASE WHEN id = %s THEN %s ELSE %s END"
        " WHERE id IN (%s, %s)",
        [a.pk, b.seq + 1000, a.seq, a.pk, b.pk],
    )
    tamper("core_chainlink", "UPDATE core_chainlink SET seq = %s WHERE id = %s", [b.seq, a.pk])

    problems = chain.verify()
    assert any("Verweis auf den Vorgänger passt nicht" in p for p in problems)
    assert any("Glied wurde verändert" in p for p in problems)


def test_deleted_link_is_detected() -> None:
    user = make_user()
    record = event(user, "mitte")
    event(user, "danach")
    middle = ChainLink.objects.get(table="accounts.AccountEvent", row_pk=str(record.pk))

    tamper("core_chainlink", "DELETE FROM core_chainlink WHERE id = %s", [middle.pk])

    problems = chain.verify()
    assert f"Glied {middle.seq} fehlt (nächstes vorhandenes: {middle.seq + 1})." in problems
    assert any("Verweis auf den Vorgänger passt nicht" in p for p in problems)
    unlinked = "Vorgang ohne Kettenglied (an der App vorbei eingefügt)."
    assert f"accounts.AccountEvent #{record.pk}: {unlinked}" in problems


def test_truncation_needs_a_known_head() -> None:
    """Wer das letzte Glied samt Vorgang löscht, fällt erst gegen den verschickten Kopf auf."""
    user = make_user()
    event(user, "vorher")
    record = event(user, "zuletzt")
    known = chain.head()
    assert known is not None
    last = links()[-1]

    tamper("core_chainlink", "DELETE FROM core_chainlink WHERE id = %s", [last.pk])
    tamper("accounts_accountevent", "DELETE FROM accounts_accountevent WHERE id = %s", [record.pk])

    assert chain.verify() == []
    assert chain.verify([known]) == [f"Kettenkopf {known.seq} fehlt: Die Kette wurde gekürzt."]


def test_recomputed_chain_differs_from_known_head() -> None:
    user = make_user()
    event(user, "a")
    known = chain.head()
    assert known is not None

    forged = Head(known.seq, "f" * 64)

    assert chain.verify([forged]) == [
        f"Kettenkopf {known.seq} weicht ab: Die Kette wurde neu berechnet."
    ]


# --- Gleichzeitig ---


@pytest.mark.django_db(transaction=True)
def test_parallel_writes_give_a_linear_chain() -> None:
    users = [make_user(f"parallel{n}") for n in range(4)]
    errors: list[BaseException] = []

    def write(user: User) -> None:
        try:
            for n in range(5):
                event(user, f"parallel {n}")
        except BaseException as error:  # im Test gesammelt und geprüft
            errors.append(error)
        finally:
            connections.close_all()

    threads = [threading.Thread(target=write, args=(user,)) for user in users]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    chained = links()
    assert [link.seq for link in chained] == list(range(1, len(chained) + 1))
    assert AccountEvent.objects.filter(detail__startswith="parallel").count() == 20
    assert chain.verify() == []


# --- Befehl ---


def test_command_reports_head_and_result() -> None:
    event(make_user(), "Befehl")
    known = chain.head()
    out = StringIO()

    call_command("kette_pruefen", f"--kopf={known}", stdout=out)

    assert f"Kettenkopf: {known}" in out.getvalue()
    assert "Kette unverändert" in out.getvalue()


def test_command_fails_on_tampering() -> None:
    record = event(make_user(), "Befehl")
    tamper(
        "accounts_accountevent",
        "UPDATE accounts_accountevent SET detail = 'x' WHERE id = %s",
        [record.pk],
    )

    with pytest.raises(CommandError, match="1 Befund"):
        call_command("kette_pruefen", stdout=StringIO(), stderr=StringIO())


def test_command_rejects_malformed_head() -> None:
    with pytest.raises(CommandError, match="Nummer"):
        call_command("kette_pruefen", "--kopf=abc", stdout=StringIO())
