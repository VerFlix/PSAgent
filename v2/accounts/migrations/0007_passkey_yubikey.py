"""Passkeys merken sich, ob ihre Herstellerbescheinigung von Yubico geprüft ist (#95)."""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0006_signaturzertifikate"),
    ]

    operations = [
        migrations.AddField(
            model_name="passkey",
            name="yubikey_attested",
            field=models.BooleanField(
                default=False,
                help_text="Nur solche Passkeys bestätigen Änderungen in der Nutzerverwaltung (#95).",
                verbose_name="YubiKey (Herstellerbescheinigung geprüft)",
            ),
        ),
    ]
