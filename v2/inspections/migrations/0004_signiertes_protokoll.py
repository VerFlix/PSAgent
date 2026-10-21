"""Signiertes Prüfprotokoll und abgelehnte Signaturen, nur zum Anhängen (#38)."""

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models

import core.append_only


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0008_yubikey_freigabe"),
        ("inspections", "0003_pruefprotokoll"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="SignatureRejection",
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
                (
                    "at",
                    models.DateTimeField(
                        default=django.utils.timezone.now, verbose_name="Zeitpunkt"
                    ),
                ),
                ("reason", models.TextField(verbose_name="Grund")),
                (
                    "uploaded_sha256",
                    models.CharField(max_length=64, verbose_name="SHA-256 der Datei"),
                ),
                (
                    "actor",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="hochgeladen von",
                    ),
                ),
                (
                    "round",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="signature_rejections",
                        to="inspections.inspectionround",
                        verbose_name="Prüfungsrunde",
                    ),
                ),
            ],
            options={
                "verbose_name": "abgelehnte Signatur",
                "verbose_name_plural": "abgelehnte Signaturen",
                "ordering": ("at", "id"),
                "constraints": [
                    models.CheckConstraint(
                        condition=models.Q(
                            ("reason__regex", "\\S"),
                            ("uploaded_sha256__regex", "^[0-9a-f]{64}$"),
                        ),
                        name="inspections_rejection_reason_and_hash",
                    )
                ],
            },
        ),
        migrations.CreateModel(
            name="SignedProtocol",
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
                (
                    "pdf",
                    models.BinaryField(verbose_name="signiertes PDF mit Zeitstempel"),
                ),
                (
                    "sha256",
                    models.CharField(
                        max_length=64, unique=True, verbose_name="SHA-256 des PDFs"
                    ),
                ),
                (
                    "uploaded_sha256",
                    models.CharField(
                        max_length=64, verbose_name="SHA-256 der hochgeladenen Datei"
                    ),
                ),
                (
                    "signed_at",
                    models.DateTimeField(verbose_name="hochgeladen und geprüft am"),
                ),
                ("stamped_at", models.DateTimeField(verbose_name="Zeitstempel")),
                (
                    "certificate",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="+",
                        to="accounts.signingcertificate",
                        verbose_name="Signaturzertifikat",
                    ),
                ),
                (
                    "protocol",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="signed",
                        to="inspections.roundprotocol",
                        verbose_name="Prüfprotokoll",
                    ),
                ),
                (
                    "round",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="signed_protocol",
                        to="inspections.inspectionround",
                        verbose_name="Prüfungsrunde",
                    ),
                ),
            ],
            options={
                "verbose_name": "signiertes Prüfprotokoll",
                "verbose_name_plural": "signierte Prüfprotokolle",
                "constraints": [
                    models.CheckConstraint(
                        condition=models.Q(
                            ("sha256__regex", "^[0-9a-f]{64}$"),
                            ("uploaded_sha256__regex", "^[0-9a-f]{64}$"),
                        ),
                        name="inspections_signed_sha256",
                    )
                ],
            },
        ),
        core.append_only.AppendOnly("signedprotocol"),
        core.append_only.AppendOnly("signaturerejection"),
    ]
