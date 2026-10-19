"""Migration `core.0002` (#15): vorhandene Zeilen werden in fester Reihenfolge mitverkettet."""

from typing import Any

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from core import chain


def leaves() -> list[tuple[str, str]]:
    nodes: list[tuple[str, str]] = MigrationExecutor(connection).loader.graph.leaf_nodes()
    return nodes


def before() -> list[tuple[str, str]]:
    """Alle anderen Apps auf dem neuesten Stand, `core` vor der Kette."""
    return [node for node in leaves() if node[0] != "core"] + [
        ("core", "0001_append_only_function")
    ]


def migrate(targets: list[tuple[str, str]]) -> Any:
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate(targets)
    return executor.loader.project_state(targets).apps


@pytest.mark.django_db(transaction=True)
def test_existing_rows_are_linked() -> None:
    old = migrate(before())
    User = old.get_model("accounts", "User")
    user = User.objects.create(username="vorher", role="viewer")
    event = old.get_model("accounts", "AccountEvent").objects.create(
        user=user, kind="stamp_changed", detail="vor der Kette"
    )

    try:
        migrate(leaves())
        from core.models import ChainLink

        [link] = ChainLink.objects.all()
        assert (link.seq, link.table, link.row_pk) == (1, "accounts.AccountEvent", str(event.pk))
        assert link.prev_hash == chain.GENESIS
        assert chain.verify() == []
    finally:
        migrate(leaves())
