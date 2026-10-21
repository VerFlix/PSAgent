"""Zertifikatsliste (#18): Signaturzertifikate und ihre Widerrufe, nur zum Anhängen."""

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models

import core.append_only


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0005_stempelprotokoll"),
        ("core", "0001_append_only_function"),
    ]

    operations = [
        migrations.CreateModel(
            name="SigningCertificate",
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
                    "name",
                    models.CharField(
                        help_text="z. B. YubiKey 1, Reserve",
                        max_length=100,
                        verbose_name="Bezeichnung",
                    ),
                ),
                ("der", models.BinaryField(verbose_name="Zertifikat (DER)")),
                (
                    "sha256",
                    models.CharField(
                        max_length=64, unique=True, verbose_name="SHA-256-Fingerabdruck"
                    ),
                ),
                (
                    "key_sha256",
                    models.CharField(
                        help_text="Ein Schlüssel steht nur einmal in der Liste, auch mit neuem Zertifikat.",
                        max_length=64,
                        unique=True,
                        verbose_name="SHA-256 des öffentlichen Schlüssels",
                    ),
                ),
                (
                    "subject",
                    models.CharField(
                        max_length=500, verbose_name="Inhaber laut Zertifikat"
                    ),
                ),
                (
                    "not_valid_before",
                    models.DateTimeField(verbose_name="Zertifikat gültig ab"),
                ),
                (
                    "not_valid_after",
                    models.DateTimeField(verbose_name="Zertifikat gültig bis"),
                ),
                (
                    "yubikey_serial",
                    models.PositiveBigIntegerField(
                        verbose_name="Seriennummer des YubiKeys"
                    ),
                ),
                (
                    "attestation",
                    models.TextField(
                        help_text="Bescheinigung für Slot 9c und die Zwischenzertifikate bis zu Yubico.",
                        verbose_name="Herstellerbescheinigung (PEM)",
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(
                        default=django.utils.timezone.now, verbose_name="aufgenommen am"
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="aufgenommen von",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="signing_certificates",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Person",
                    ),
                ),
            ],
            options={
                "verbose_name": "Signaturzertifikat",
                "verbose_name_plural": "Signaturzertifikate",
                "ordering": ("created_at", "id"),
            },
        ),
        migrations.CreateModel(
            name="SigningCertificateRevocation",
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
                    "revoked_at",
                    models.DateTimeField(
                        default=django.utils.timezone.now, verbose_name="widerrufen ab"
                    ),
                ),
                ("reason", models.TextField(verbose_name="Begründung")),
                (
                    "created_at",
                    models.DateTimeField(
                        default=django.utils.timezone.now, verbose_name="eingetragen am"
                    ),
                ),
                (
                    "certificate",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="revocation",
                        to="accounts.signingcertificate",
                        verbose_name="Zertifikat",
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="widerrufen von",
                    ),
                ),
            ],
            options={
                "verbose_name": "Widerruf eines Signaturzertifikats",
                "verbose_name_plural": "Widerrufe von Signaturzertifikaten",
            },
        ),
        migrations.AddConstraint(
            model_name="signingcertificate",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("sha256__regex", "^[0-9a-f]{64}$"),
                    ("key_sha256__regex", "^[0-9a-f]{64}$"),
                ),
                name="accounts_certificate_sha256",
            ),
        ),
        migrations.AddConstraint(
            model_name="signingcertificate",
            constraint=models.CheckConstraint(
                condition=models.Q(("name__regex", "\\S")),
                name="accounts_certificate_name_required",
            ),
        ),
        migrations.AddConstraint(
            model_name="signingcertificate",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("not_valid_after__gt", models.F("not_valid_before"))
                ),
                name="accounts_certificate_period_valid",
            ),
        ),
        migrations.AddConstraint(
            model_name="signingcertificaterevocation",
            constraint=models.CheckConstraint(
                condition=models.Q(("reason__regex", "\\S")),
                name="accounts_certificate_revocation_reason",
            ),
        ),
        migrations.AddConstraint(
            model_name="signingcertificaterevocation",
            constraint=models.CheckConstraint(
                condition=models.Q(("revoked_at__lte", models.F("created_at"))),
                name="accounts_certificate_revocation_not_future",
            ),
        ),
        core.append_only.AppendOnly("signingcertificate"),
        core.append_only.AppendOnly("signingcertificaterevocation"),
    ]
