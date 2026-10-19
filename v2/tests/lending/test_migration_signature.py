"""Migration `lending.0005` (#63): vorhandene Unterschriften wandern samt Prüfsumme um."""

from typing import Any

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from lending.models import signature_digest

BEFORE = [("lending", "0004_papierunterschrift")]
AFTER = [("lending", "0005_unterschrift_ausgelagert")]
STROKES = [[[10, 10], [20, 15.5]]]


def migrate(targets: list[tuple[str, str]]) -> Any:
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate(targets)
    return executor.loader.project_state(targets).apps


@pytest.mark.django_db(transaction=True)
def test_existing_signatures_move_with_digest() -> None:
    old = migrate(BEFORE)
    signature = old.get_model("lending", "Signature").objects.create(
        strokes=STROKES, signer_name="Kim Beispiel", origin="legacy"
    )

    try:
        new = migrate(AFTER)
        migrated = new.get_model("lending", "Signature").objects.get(pk=signature.pk)
        content = new.get_model("lending", "SignatureContent").objects.get(signature=migrated)
        assert (content.signer_name, content.strokes, migrated.origin) == (
            "Kim Beispiel",
            STROKES,
            "legacy",
        )
        assert migrated.content_sha256 == signature_digest(content.salt, "Kim Beispiel", STROKES)
        with connection.cursor() as cursor, pytest.raises(Exception, match="nur zum Anhängen"):
            cursor.execute("UPDATE lending_signature SET origin = 'app'")
    finally:
        migrate(MigrationExecutor(connection).loader.graph.leaf_nodes())
