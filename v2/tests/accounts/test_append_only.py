"""Sachkunde-Einträge und Widerrufe werden nur angehängt, nie geändert oder gelöscht.

Geprüft wird über das ORM (klare Fehlermeldung) und direkt per SQL (Trigger in PostgreSQL),
damit auch ein Zugriff an der Anwendung vorbei nichts ändert (CLAUDE.md, Regel 3).
"""

from datetime import timedelta

import pytest
from django.db import IntegrityError, connection, transaction
from django.db.models import ProtectedError

from accounts.models import Qualification, QualificationRevocation, Role
from core.append_only import AppendOnly, AppendOnlyError
from tests.accounts.conftest import TODAY, Grant, MakeUser

DAY = timedelta(days=1)


@pytest.fixture
def entries(make_user: MakeUser, grant: Grant) -> tuple[Qualification, QualificationRevocation]:
    qualification = grant(make_user(role=Role.PPE_EXPERT), TODAY - DAY, TODAY + 300 * DAY)
    revocation = QualificationRevocation.objects.create(
        qualification=qualification, reason="Testwiderruf"
    )
    return qualification, revocation


def test_saving_a_changed_qualification_fails(
    entries: tuple[Qualification, QualificationRevocation],
) -> None:
    qualification, _ = entries
    qualification.valid_until = TODAY + 900 * DAY

    with pytest.raises(AppendOnlyError):
        qualification.save()

    qualification.refresh_from_db()
    assert qualification.valid_until == TODAY + 300 * DAY


@pytest.mark.parametrize("model", [Qualification, QualificationRevocation])
def test_orm_update_and_delete_fail(
    model: type[Qualification] | type[QualificationRevocation],
    entries: tuple[Qualification, QualificationRevocation],
) -> None:
    instance = model.objects.get()

    with pytest.raises(AppendOnlyError):
        model.objects.all().update(id=instance.pk)
    with pytest.raises(AppendOnlyError):
        model.objects.all().delete()
    with pytest.raises(AppendOnlyError):
        instance.delete()

    assert model.objects.count() == 1


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE accounts_qualification SET valid_until = valid_until + 365",
        "DELETE FROM accounts_qualification",
        "UPDATE accounts_qualificationrevocation SET reason = 'geändert'",
        "DELETE FROM accounts_qualificationrevocation",
    ],
)
def test_database_rejects_update_and_delete(
    sql: str, entries: tuple[Qualification, QualificationRevocation]
) -> None:
    with (
        pytest.raises(IntegrityError, match="nur zum Anhängen"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(sql)

    assert Qualification.objects.count() == 1
    assert QualificationRevocation.objects.count() == 1


def test_account_with_qualification_cannot_be_deleted(
    entries: tuple[Qualification, QualificationRevocation],
) -> None:
    qualification, _ = entries

    with pytest.raises(ProtectedError):
        qualification.user.delete()


def test_append_only_operation_round_trips_for_migrations() -> None:
    operation = AppendOnly("qualification")

    name, args, kwargs = operation.deconstruct()

    assert (name, args, kwargs) == ("AppendOnly", ["qualification"], {})
    assert "qualification" in operation.describe()
