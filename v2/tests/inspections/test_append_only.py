"""Prüf- und Vorgangstabellen: kein UPDATE, kein DELETE, auch nicht per SQL (Abnahme #8)."""

import pytest
from django.db import IntegrityError, connection, transaction

from accounts.models import User
from inspections.models import InspectionResult, InspectionRound, RoundEvent
from inventory.models import PartEvent
from tests.helpers import signed_inspection
from tests.inspections.conftest import MakePart

TABLES = (
    "inspections_inspectionround",
    "inspections_inspectionresult",
    "inspections_roundevent",
    "inventory_partevent",
)
UPDATES = {
    "inspections_inspectionround": "inspector_name = 'geändert'",
    "inspections_inspectionresult": "next_due = next_due + 3650",
    "inspections_roundevent": "comment = 'geändert'",
    "inventory_partevent": "comment = 'geändert'",
}


@pytest.fixture
def signed(make_part: MakePart, inspector: User) -> None:
    signed_inspection(inspector, [make_part(), make_part()])


@pytest.mark.parametrize("table", TABLES)
@pytest.mark.parametrize("statement", ["UPDATE {table} SET {update}", "DELETE FROM {table}"])
def test_sql_update_and_delete_fail(table: str, statement: str, signed: None) -> None:
    sql = statement.format(table=table, update=UPDATES[table])

    with (
        pytest.raises(IntegrityError, match="nur zum Anhängen"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(sql)


@pytest.mark.parametrize("model", [InspectionRound, InspectionResult, RoundEvent, PartEvent])
def test_orm_update_and_delete_fail(model: type[InspectionRound], signed: None) -> None:
    entry = model.objects.first()
    assert entry is not None

    with pytest.raises(IntegrityError, match="nur zum Anhängen"):
        entry.save()
    with pytest.raises(IntegrityError, match="nur zum Anhängen"):
        entry.delete()
    with pytest.raises(IntegrityError, match="nur zum Anhängen"):
        model.objects.all().update()
    with pytest.raises(IntegrityError, match="nur zum Anhängen"):
        model.objects.all().delete()
    with pytest.raises(IntegrityError, match="nur zum Anhängen"), transaction.atomic():
        model.objects.filter(pk=entry.pk)._raw_delete(using="default")
