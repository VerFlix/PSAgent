"""Prüfprotokoll einer abgeschlossenen Runde als PDF, nur zum Anhängen (#38)."""

import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models

import core.append_only


class Migration(migrations.Migration):

    dependencies = [
        ("inspections", "0002_pruefmodus"),
    ]

    operations = [
        migrations.CreateModel(
            name="RoundProtocol",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("pdf", models.BinaryField(verbose_name="PDF")),
                (
                    "sha256",
                    models.CharField(
                        max_length=64, unique=True, verbose_name="SHA-256 des PDFs"
                    ),
                ),
                (
                    "content_sha256",
                    models.CharField(
                        max_length=64, verbose_name="SHA-256 des Inhalts der Runde"
                    ),
                ),
                (
                    "chain_seq",
                    models.PositiveBigIntegerField(verbose_name="Kettenkopf: Nummer"),
                ),
                (
                    "chain_hash",
                    models.CharField(max_length=64, verbose_name="Kettenkopf: Hash"),
                ),
                (
                    "created_at",
                    models.DateTimeField(
                        default=django.utils.timezone.now, verbose_name="erzeugt am"
                    ),
                ),
                (
                    "round",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="protocol",
                        to="inspections.inspectionround",
                        verbose_name="Prüfungsrunde",
                    ),
                ),
            ],
            options={
                "verbose_name": "Prüfprotokoll",
                "verbose_name_plural": "Prüfprotokolle",
                "constraints": [
                    models.CheckConstraint(
                        condition=models.Q(
                            ("sha256__regex", "^[0-9a-f]{64}$"),
                            ("content_sha256__regex", "^[0-9a-f]{64}$"),
                            ("chain_hash__regex", "^[0-9a-f]{64}$"),
                        ),
                        name="inspections_protocol_sha256",
                    )
                ],
            },
        ),
        core.append_only.AppendOnly("roundprotocol"),
    ]
