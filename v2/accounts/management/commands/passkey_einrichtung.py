"""Management-Befehl: Einmal-Link zum Einrichten des ersten Passkeys erzeugen.

Der Link steht im Protokoll der Kontoverwaltung (ohne „durch“); ältere offene Links verfallen.
"""

from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.urls import reverse

from accounts.models import User
from accounts.services import issue_enrollment


class Command(BaseCommand):
    help = (
        "Erzeugt einen Einmal-Link, mit dem ein Konto seinen ersten Passkey einrichtet "
        "(Notzugang für Admin und PSA-Sachkundige, die sich nicht mit Passwort anmelden)."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("benutzername")
        parser.add_argument(
            "--stunden", type=int, default=None, help="Gültigkeit in Stunden (Standard: 24)"
        )

    def handle(self, *args: Any, **options: Any) -> None:
        user = User.objects.filter(username=options["benutzername"], is_active=True).first()
        if user is None:
            raise CommandError("Kein aktives Konto mit diesem Benutzernamen.")
        if options["stunden"] is not None and not 1 <= options["stunden"] <= 72:
            raise CommandError("--stunden muss zwischen 1 und 72 liegen.")
        hours = options["stunden"] or settings.PSAGENT_ENROLLMENT_HOURS
        token = issue_enrollment(user, actor=None, hours=hours)
        path = reverse("accounts:enroll", args=[token])
        self.stdout.write(f"Einrichtungslink (einmal gültig): {path}")
        self.stdout.write("Den Link nur persönlich weitergeben; er ersetzt die Anmeldung.")
