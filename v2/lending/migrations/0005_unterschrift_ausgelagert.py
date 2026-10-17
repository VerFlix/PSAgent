"""Name und Strichliste der Unterschrift in eine löschbare Tabelle auslagern (#63).

`lending_signature` wird nur angehängt; ihr Trigger verbietet UPDATE und DELETE. Diese
Migration ist die einzige Stelle, an der die Tabelle einmalig umgebaut wird:

1. `SignatureContent` anlegen und für jede vorhandene Unterschrift Salt, Name und Strichliste
   dorthin kopieren.
2. Die Prüfsumme in die neue Spalte `content_sha256` schreiben. Dafür ist der Trigger nur
   innerhalb dieser Migration und nur für diese eine Anweisung ausgeschaltet.
3. Die Spalten mit Personendaten (`signer_name`, `strokes`) entfernen.

Die Migration läuft vor dem echten Import (#29); im Betrieb gibt es noch keine Unterschriften.
Rückwärts läuft sie nur ohne Unterschriften (Entwicklung, Tests); gelöschte Inhalte kommen
nicht zurück.
"""

import hashlib
import json
import secrets
from typing import Any

import django.db.models.deletion
from django.db import migrations, models

TABLE = "lending_signature"
TRIGGER = "lending_signature_append_only"


def digest(salt: str, signer_name: str, strokes: Any) -> str:
    """Wie `lending.models.signature_digest`; hier festgeschrieben, damit die Migration stabil bleibt."""
    canonical = json.dumps(
        {"salt": salt, "signer_name": signer_name, "strokes": strokes},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def move_content(apps: Any, schema_editor: Any) -> None:
    Signature = apps.get_model("lending", "Signature")
    SignatureContent = apps.get_model("lending", "SignatureContent")
    digests = []
    for signature in Signature.objects.order_by("pk").iterator():
        salt = secrets.token_hex(16)
        SignatureContent.objects.create(
            signature_id=signature.pk,
            salt=salt,
            signer_name=signature.signer_name,
            strokes=signature.strokes,
        )
        digests.append((digest(salt, signature.signer_name, signature.strokes), signature.pk))
    if not digests:
        return
    quote = schema_editor.quote_name
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(f"ALTER TABLE {quote(TABLE)} DISABLE TRIGGER {quote(TRIGGER)}")
        cursor.executemany(
            f"UPDATE {quote(TABLE)} SET content_sha256 = %s WHERE id = %s",  # noqa: S608
            digests,
        )
        cursor.execute(f"ALTER TABLE {quote(TABLE)} ENABLE TRIGGER {quote(TRIGGER)}")


def only_without_signatures(apps: Any, schema_editor: Any) -> None:
    if apps.get_model("lending", "Signature").objects.exists():
        raise RuntimeError("Mit vorhandenen Unterschriften lässt sich 0005 nicht zurücknehmen.")


class Migration(migrations.Migration):

    dependencies = [
        ("lending", "0004_papierunterschrift"),
    ]

    operations = [
        migrations.CreateModel(
            name="SignatureContent",
            fields=[
                (
                    "signature",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        primary_key=True,
                        related_name="content",
                        serialize=False,
                        to="lending.signature",
                        verbose_name="Unterschrift",
                    ),
                ),
                ("salt", models.CharField(max_length=32, verbose_name="Salt")),
                (
                    "signer_name",
                    models.CharField(
                        max_length=200, verbose_name="Name der unterschreibenden Person"
                    ),
                ),
                ("strokes", models.JSONField(verbose_name="Striche")),
            ],
            options={
                "verbose_name": "Inhalt einer Unterschrift",
                "verbose_name_plural": "Inhalte von Unterschriften",
            },
        ),
        migrations.AddField(
            model_name="signature",
            name="content_sha256",
            field=models.CharField(
                max_length=64, null=True, verbose_name="SHA-256 von Name und Unterschrift"
            ),
        ),
        migrations.RunPython(move_content, only_without_signatures),
        migrations.RemoveField(
            model_name="signature",
            name="signer_name",
        ),
        migrations.RemoveField(
            model_name="signature",
            name="strokes",
        ),
        migrations.AlterField(
            model_name="signature",
            name="content_sha256",
            field=models.CharField(max_length=64, verbose_name="SHA-256 von Name und Unterschrift"),
        ),
        migrations.AddConstraint(
            model_name="signature",
            constraint=models.CheckConstraint(
                condition=models.Q(("content_sha256__regex", "^[0-9a-f]{64}$")),
                name="lending_signature_sha256",
            ),
        ),
    ]
