"""Tabellen, die nur angehängt werden: kein UPDATE, kein DELETE (CLAUDE.md, Regel 3).

Zwei Schichten:

- In der Datenbank verhindert ein Trigger jedes UPDATE und DELETE, auch an der Anwendung
  vorbei. Die Trigger-Funktion `psagent_append_only()` legt die Migration `core.0001` an; die
  Migrationsoperation `AppendOnly` hängt sie an die Tabelle eines Modells.
- In Django geben `AppendOnlyModel` und sein QuerySet schon vorher eine klare Fehlermeldung.

Korrekturen sind neue Einträge mit Verweis auf den alten. TRUNCATE blockiert der Trigger
nicht, weil die Testumgebung Tabellen damit leert. Im Betrieb entzieht die Rechtevergabe des
Datenbanknutzers TRUNCATE (Einrichtung des Pi, Phase 4).
"""

from typing import Any, ClassVar, TypeVar

from django.db import IntegrityError, models
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.operations.base import Operation
from django.db.migrations.state import ProjectState

FUNCTION_NAME = "psagent_append_only"

_M = TypeVar("_M", bound=models.Model)


class AppendOnlyError(IntegrityError):
    """Ein Eintrag einer Tabelle, die nur angehängt wird, sollte geändert oder gelöscht werden."""


def _message(model: type[models.Model], action: str) -> str:
    return (
        f"{model._meta.label} ist nur zum Anhängen: {action} ist nicht erlaubt. "
        "Korrekturen sind neue Einträge mit Verweis auf den alten."
    )


class AppendOnlyQuerySet(models.QuerySet[_M]):
    """QuerySet ohne update() und delete()."""

    def update(self, **kwargs: Any) -> int:
        raise AppendOnlyError(_message(self.model, "Ändern"))

    def delete(self) -> tuple[int, dict[str, int]]:
        raise AppendOnlyError(_message(self.model, "Löschen"))


class AppendOnlyModel(models.Model):
    """Basis für Modelle, deren Einträge nur angehängt werden.

    Die Tabelle braucht zusätzlich die Migrationsoperation `AppendOnly`, damit auch die
    Datenbank Änderungen verhindert.
    """

    objects: ClassVar[models.Manager[Any]] = AppendOnlyQuerySet.as_manager()

    class Meta:
        abstract = True

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise AppendOnlyError(_message(type(self), "Ändern"))
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        raise AppendOnlyError(_message(type(self), "Löschen"))


class AppendOnly(Operation):
    """Migrationsoperation: schützt die Tabelle eines Modells per Trigger vor UPDATE und DELETE."""

    reduces_to_sql = True
    reversible = True

    def __init__(self, model_name: str) -> None:
        self.model_name = model_name

    def deconstruct(self) -> tuple[str, list[str], dict[str, Any]]:
        return (self.__class__.__qualname__, [self.model_name], {})

    def state_forwards(self, app_label: str, state: ProjectState) -> None:
        """Der Modellzustand ändert sich nicht, nur die Datenbank."""

    @staticmethod
    def _trigger(schema_editor: BaseDatabaseSchemaEditor, table: str) -> tuple[str, str]:
        if schema_editor.connection.vendor != "postgresql":
            raise NotImplementedError("Der Schutz „nur anhängen“ gibt es nur für PostgreSQL.")
        quote = schema_editor.quote_name
        return quote(f"{table}_append_only"), quote(table)

    def database_forwards(
        self,
        app_label: str,
        schema_editor: BaseDatabaseSchemaEditor,
        from_state: ProjectState,
        to_state: ProjectState,
    ) -> None:
        model = to_state.apps.get_model(app_label, self.model_name)
        if not self.allow_migrate_model(schema_editor.connection.alias, model):
            return
        trigger, table = self._trigger(schema_editor, model._meta.db_table)
        schema_editor.execute(
            f"CREATE TRIGGER {trigger} BEFORE UPDATE OR DELETE ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION {FUNCTION_NAME}()"
        )

    def database_backwards(
        self,
        app_label: str,
        schema_editor: BaseDatabaseSchemaEditor,
        from_state: ProjectState,
        to_state: ProjectState,
    ) -> None:
        model = from_state.apps.get_model(app_label, self.model_name)
        if not self.allow_migrate_model(schema_editor.connection.alias, model):
            return
        trigger, table = self._trigger(schema_editor, model._meta.db_table)
        schema_editor.execute(f"DROP TRIGGER IF EXISTS {trigger} ON {table}")

    def describe(self) -> str:
        return f"Tabelle von {self.model_name} nur zum Anhängen (Trigger gegen UPDATE/DELETE)"

    @property
    def migration_name_fragment(self) -> str:
        return f"append_only_{self.model_name.lower()}"
