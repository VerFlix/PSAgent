"""YubiKeys bestätigen Änderungen erst nach der Freigabe (#95)."""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0007_passkey_yubikey"),
    ]

    operations = [
        migrations.AddField(
            model_name="passkey",
            name="yubikey_approved_at",
            field=models.DateTimeField(
                blank=True,
                help_text="Erst freigegeben bestätigt ein YubiKey Änderungen: mit einem schon freigegebenen YubiKey desselben Kontos oder auf dem Server (manage.py yubikey_freigeben).",
                null=True,
                verbose_name="als YubiKey freigegeben am",
            ),
        ),
    ]
