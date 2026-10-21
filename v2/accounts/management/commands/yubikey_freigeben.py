"""Management-Befehl: den ersten YubiKey eines Kontos für Bestätigungen freigeben (#95).

Weitere YubiKeys gibt ein schon freigegebener YubiKey beim Einrichten frei. Den ersten nur hier
auf dem Server: Wer bloß eine fremde Sitzung übernimmt, kann so keinen eigenen YubiKey
unterschieben. Freigegeben werden nur Passkeys mit geprüfter Herstellerbescheinigung von Yubico.
"""

from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.utils import timezone

from accounts.models import User


class Command(BaseCommand):
    help = (
        "Gibt die bescheinigten YubiKey-Passkeys eines Kontos frei, damit sie Änderungen in der "
        "Nutzerverwaltung bestätigen. Ohne --passkey nur anzeigen."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("benutzername")
        parser.add_argument(
            "--passkey", type=int, action="append", help="Nummer des Passkeys (mehrfach möglich)"
        )

    def handle(self, *args: Any, **options: Any) -> None:
        user = User.objects.filter(username=options["benutzername"], is_active=True).first()
        if user is None:
            raise CommandError("Kein aktives Konto mit diesem Benutzernamen.")
        candidates = user.passkeys.filter(
            revoked_at__isnull=True, yubikey_attested=True, yubikey_approved_at__isnull=True
        ).order_by("created_at")
        chosen = options["passkey"] or []
        if not chosen:
            if not candidates:
                self.stdout.write("Keine bescheinigten YubiKeys ohne Freigabe.")
            for passkey in candidates:
                self.stdout.write(
                    f"{passkey.pk}: {passkey.name} (eingerichtet "
                    f"{timezone.localtime(passkey.created_at):%d.%m.%Y %H:%M})"
                )
            self.stdout.write("Freigeben mit --passkey <Nummer>, nur für selbst eingerichtete.")
            return
        found = {passkey.pk: passkey for passkey in candidates.filter(pk__in=chosen)}
        missing = sorted(set(chosen) - set(found))
        if missing:
            raise CommandError(f"Kein freizugebender YubiKey mit Nummer {missing[0]}.")
        for passkey in found.values():
            passkey.yubikey_approved_at = timezone.now()
            passkey.save(update_fields=["yubikey_approved_at"])
            self.stdout.write(f"Freigegeben: {passkey.name}")
