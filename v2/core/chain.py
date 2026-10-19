"""Hash-Kette über alle Vorgänge (#15, Konzept „Hash-Kette und Zeuge“).

Jede neue Zeile einer Tabelle, die nur angehängt wird (`AppendOnlyModel`), bekommt in derselben
Transaktion ein Kettenglied (`ChainLink`). Das Glied hält:

- die Prüfsumme der Zeile (`payload_sha256`) über ihre Spalten in fester JSON-Form,
- den Hash des Vorgängers (`prev_hash`) und daraus den eigenen Hash.

Eine PostgreSQL-Sperre (`pg_advisory_xact_lock`) reiht gleichzeitige Vorgänge ein: Die Kette
bleibt linear, ohne Lücken und ohne Verzweigung. Bricht eine Transaktion ab, verschwindet ihr
Glied mit ihr, und die Nummer wird neu vergeben.

Die Kette speichert nur Prüfsummen, keine Klardaten. Personendaten, die gelöscht werden dürfen,
stehen nicht in Tabellen, die nur angehängt werden (#63); dort steht höchstens ihr gesalzener Hash.

`verify()` erkennt geänderte, gelöschte, eingefügte und vertauschte Vorgänge. Wer die Datenbank
vollständig neu berechnen kann, fällt erst im Vergleich mit einem früher verschickten
Kettenkopf auf (`known_head`); dafür gehen die Köpfe täglich an den Vorstand (#17).

Prüfsummen: SHA-256 aus `hashlib` (OpenSSL), keine eigene Kryptografie (CLAUDE.md, Regel 6).
Das Format ist festgeschrieben (`FORMAT`); eine Änderung braucht ein neues Format, nie eine
Änderung von Format 1, sonst passen alte Glieder nicht mehr.
"""

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID

from django.db import connection, models

if TYPE_CHECKING:
    from core.models import ChainLink

FORMAT = 1
GENESIS = "0" * 64
# Schlüssel der PostgreSQL-Sperre, die Kettenglieder nacheinander vergibt („PSAK“).
LOCK_KEY = 0x5053414B


def _value(value: Any) -> Any:
    """Spaltenwert als JSON-Wert; unbekannte Typen sind ein Fehler, damit nichts verloren geht."""
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, datetime | date | time):
        return value.isoformat()
    if isinstance(value, Decimal | UUID):
        return str(value)
    if isinstance(value, bytes | memoryview):
        return bytes(value).hex()
    if isinstance(value, float | list | dict):
        return value
    raise TypeError(f"Spaltentyp {type(value).__name__} ist für die Hash-Kette nicht festgelegt.")


def _digest(data: dict[str, Any]) -> str:
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


def payload_digest(table: str, pk: Any, row: dict[str, Any]) -> str:
    """Prüfsumme einer Zeile über die angegebenen Spalten."""
    return _digest(
        {"table": table, "pk": _value(pk), "row": {name: _value(v) for name, v in row.items()}}
    )


def link_digest(
    *, seq: int, prev: str, table: str, pk: Any, fields: list[str], payload: str, at: datetime
) -> str:
    """Hash eines Kettenglieds; enthält den Hash des Vorgängers."""
    return _digest(
        {
            "format": FORMAT,
            "seq": seq,
            "prev": prev,
            "table": table,
            "pk": _value(pk),
            "fields": fields,
            "payload": payload,
            "at": at.isoformat(),
        }
    )


def fields_of(model: type[models.Model]) -> list[str]:
    return sorted(field.attname for field in model._meta.concrete_fields)


def _row(model: type[models.Model], pk: Any, fields: list[str]) -> dict[str, Any] | None:
    """Die Zeile, wie sie in der Datenbank steht (nicht wie sie im Speicher war)."""
    return model._base_manager.filter(pk=pk).values(*fields).first()


def link(obj: models.Model) -> "ChainLink":
    """Hängt ein Kettenglied für die eben gespeicherte Zeile an; nur innerhalb einer Transaktion."""
    from core.models import ChainLink

    model = type(obj)
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", [LOCK_KEY])
    last = ChainLink.objects.order_by("-seq").first()
    seq, prev = (last.seq + 1, last.hash) if last else (1, GENESIS)
    table, fields = model._meta.label, fields_of(model)
    row = _row(model, obj.pk, fields)
    assert row is not None  # noqa: S101 – eben in derselben Transaktion gespeichert
    payload = payload_digest(table, str(obj.pk), row)
    entry = ChainLink(
        seq=seq,
        prev_hash=prev,
        table=table,
        row_pk=str(obj.pk),
        fields=fields,
        payload_sha256=payload,
    )
    entry.hash = link_digest(
        seq=seq,
        prev=prev,
        table=table,
        pk=entry.row_pk,
        fields=fields,
        payload=payload,
        at=entry.at,
    )
    # Gleich in die Tabelle (Trigger schützt sie); `AppendOnlyModel.save` brächte nichts Neues.
    models.Model.save(entry, force_insert=True)
    return entry


# --- Prüfen ---


@dataclass(frozen=True)
class Head:
    """Kettenkopf: Nummer und Hash des letzten Glieds."""

    seq: int
    hash: str

    def __str__(self) -> str:
        return f"{self.seq}:{self.hash}"

    @classmethod
    def parse(cls, text: str) -> "Head":
        seq, _, digest = text.strip().partition(":")
        if not seq.isdigit() or len(digest) != 64:
            raise ValueError("Kettenkopf bitte als <Nummer>:<SHA-256> angeben.")
        return cls(int(seq), digest.lower())


def head() -> Head | None:
    from core.models import ChainLink

    last = ChainLink.objects.order_by("-seq").first()
    return Head(last.seq, last.hash) if last else None


def chained_models() -> list[type[models.Model]]:
    """Alle Modelle, deren Zeilen in die Kette gehören."""
    from django.apps import apps

    from core.append_only import AppendOnlyModel
    from core.models import ChainLink

    return [
        model
        for model in apps.get_models()
        if issubclass(model, AppendOnlyModel) and model is not ChainLink
    ]


def verify(known_heads: Iterable[Head] = ()) -> list[str]:
    """Prüft die ganze Kette; leer = unverändert. Meldungen nennen Tabelle und Nummer.

    `known_heads` sind früher festgehaltene Köpfe (z. B. aus der Zeugen-Mail an den Vorstand);
    jeder muss noch in der Kette stehen.
    """
    from core.models import ChainLink

    problems: list[str] = []
    models_by_label = {model._meta.label: model for model in chained_models()}
    linked: dict[str, set[str]] = {label: set() for label in models_by_label}
    hashes: dict[int, str] = {}
    prev, expected_seq = GENESIS, 1
    for entry in ChainLink.objects.order_by("seq").iterator():
        where = f"Glied {entry.seq} ({entry.table} #{entry.row_pk})"
        if entry.seq != expected_seq:
            problems.append(f"Glied {expected_seq} fehlt (nächstes vorhandenes: {entry.seq}).")
        if entry.prev_hash != prev:
            problems.append(f"{where}: Verweis auf den Vorgänger passt nicht.")
        recomputed = link_digest(
            seq=entry.seq,
            prev=entry.prev_hash,
            table=entry.table,
            pk=entry.row_pk,
            fields=list(entry.fields),
            payload=entry.payload_sha256,
            at=entry.at,
        )
        if recomputed != entry.hash:
            problems.append(f"{where}: Glied wurde verändert.")
        model = models_by_label.get(entry.table)
        if model is None:
            problems.append(f"{where}: unbekannte Tabelle.")
        else:
            linked[entry.table].add(entry.row_pk)
            row = _row(model, entry.row_pk, list(entry.fields))
            if row is None:
                problems.append(f"{where}: Vorgang wurde gelöscht.")
            elif payload_digest(entry.table, entry.row_pk, row) != entry.payload_sha256:
                problems.append(f"{where}: Vorgang wurde geändert.")
        hashes[entry.seq] = entry.hash
        prev, expected_seq = entry.hash, entry.seq + 1
    for label, model in models_by_label.items():
        stored = {str(pk) for pk in model._base_manager.values_list("pk", flat=True)}
        for pk in sorted(stored - linked[label]):
            problems.append(
                f"{label} #{pk}: Vorgang ohne Kettenglied (an der App vorbei eingefügt)."
            )
    for known in known_heads:
        if known.seq not in hashes:
            problems.append(f"Kettenkopf {known.seq} fehlt: Die Kette wurde gekürzt.")
        elif hashes[known.seq] != known.hash:
            problems.append(f"Kettenkopf {known.seq} weicht ab: Die Kette wurde neu berechnet.")
    return problems
