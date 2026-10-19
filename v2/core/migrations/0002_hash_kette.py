"""Hash-Kette (#15): Tabelle der Kettenglieder; vorhandene Zeilen werden mitverkettet.

Welche Tabellen dazugehören, sagt die Datenbank selbst: alle mit dem Trigger
`<tabelle>_append_only`. Die Reihenfolge ist fest (Tabelle, dann Schlüssel), damit die
Migration auf jeder Datenbank dieselbe Kette ergibt. Die Prüfsummen rechnet `core.chain` im
Format 1, das nie geändert wird (siehe dort).

Im Betrieb gibt es noch keine echten Daten; der echte Import (#29) läuft danach schon verkettet.
"""

from typing import Any

import django.utils.timezone
from django.db import migrations, models

import core.append_only


def link_existing(apps: Any, schema_editor: Any) -> None:
    from core import chain

    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            "SELECT event_object_table FROM information_schema.triggers "
            "WHERE trigger_name = event_object_table || '_append_only'"
        )
        tables = {row[0] for row in cursor.fetchall()} - {"core_chainlink"}
    ChainLink = apps.get_model("core", "ChainLink")
    prev, seq = chain.GENESIS, 0
    for model in sorted(
        (m for m in apps.get_models() if m._meta.db_table in tables),
        key=lambda m: m._meta.label,
    ):
        label = model._meta.label
        fields = sorted(field.attname for field in model._meta.concrete_fields)
        for row in model._base_manager.order_by("pk").values(*fields).iterator():
            seq += 1
            pk = str(row[model._meta.pk.attname])
            payload = chain.payload_digest(label, pk, row)
            at = django.utils.timezone.now()
            digest = chain.link_digest(
                seq=seq, prev=prev, table=label, pk=pk, fields=fields, payload=payload, at=at
            )
            ChainLink.objects.create(
                seq=seq,
                prev_hash=prev,
                table=label,
                row_pk=pk,
                fields=fields,
                payload_sha256=payload,
                hash=digest,
                at=at,
            )
            prev = digest


def only_without_links(apps: Any, schema_editor: Any) -> None:
    if apps.get_model("core", "ChainLink").objects.exists():
        raise RuntimeError("Mit vorhandenen Kettengliedern lässt sich 0002 nicht zurücknehmen.")


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0001_append_only_function"),
        ("accounts", "0005_stempelprotokoll"),
        ("inventory", "0003_partevent_inspection_result"),
        ("inspections", "0001_initial"),
        ("lending", "0006_erinnerungen"),
        ("emergency", "0004_ausleihen_aus_formular"),
        ("legacy", "0001_initial"),
        ("privacy", "0001_loeschsperre_und_protokoll"),
    ]

    operations = [
        migrations.CreateModel(
            name="ChainLink",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("seq", models.PositiveBigIntegerField(unique=True, verbose_name="Nummer")),
                ("prev_hash", models.CharField(max_length=64, verbose_name="Hash des Vorgängers")),
                ("table", models.CharField(max_length=100, verbose_name="Tabelle")),
                ("row_pk", models.CharField(max_length=64, verbose_name="Schlüssel der Zeile")),
                ("fields", models.JSONField(verbose_name="Spalten")),
                ("payload_sha256", models.CharField(max_length=64, verbose_name="SHA-256 der Zeile")),
                ("hash", models.CharField(max_length=64, unique=True, verbose_name="Hash")),
                ("at", models.DateTimeField(default=django.utils.timezone.now, verbose_name="Zeitpunkt")),
            ],
            options={
                "verbose_name": "Kettenglied",
                "verbose_name_plural": "Kettenglieder",
                "ordering": ("seq",),
                "constraints": [
                    models.UniqueConstraint(fields=("table", "row_pk"), name="core_chain_row_once"),
                    models.CheckConstraint(
                        condition=models.Q(
                            ("hash__regex", "^[0-9a-f]{64}$"),
                            ("prev_hash__regex", "^[0-9a-f]{64}$"),
                            ("payload_sha256__regex", "^[0-9a-f]{64}$"),
                        ),
                        name="core_chain_sha256",
                    ),
                ],
            },
        ),
        core.append_only.AppendOnly("chainlink"),
        migrations.RunPython(link_existing, only_without_links),
    ]
