"""Täglicher Zeuge für die Hash-Kette (#16, #17, Konzept „Hash-Kette und Zeuge“).

`manage.py kettenkopf` hält einmal am Tag den Kettenkopf fest:

1. Zeitstempel nach RFC 3161 von einem externen Dienst holen (`core/timestamp.py`).
2. Kopf und Stempel als `WitnessHead` ablegen (nur anhängen, selbst Teil der Kette).
3. Als Textdatei in `PSAGENT_WITNESS_DIR` schreiben (auf Unraid gespiegelt, Phase 4).
4. Per E-Mail an das Zeugen-Postfach des Vorstands schicken (`PSAGENT_WITNESS_TO`).

Damit fällt eine neu berechnete Kette gleich doppelt auf: Ihre Köpfe passen nicht zu den
verschickten, und für frühere Tage gibt es keine passenden Stempel, weil niemand rückdatieren
kann. Mail und Datei enthalten nur Nummer, Hash und Stempel, keine Personendaten.
"""

import base64
import textwrap
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.mail import EmailMessage
from django.utils import timezone

from core import chain, timestamp
from core.models import ChainLink, WitnessHead

# So lange darf der letzte gültige Zeitstempel höchstens zurückliegen.
MAX_GAP = timedelta(days=2)


class WitnessError(RuntimeError):
    """Ein Schritt des täglichen Laufs ist gescheitert; der Text sagt, welcher."""


def capture() -> tuple[WitnessHead, str | None]:
    """Hält den aktuellen Kopf fest; ohne Stempel, wenn der Dienst ausfällt (dann mit Grund)."""
    current = chain.head()
    if current is None:
        raise WitnessError("Die Kette ist leer; es gibt keinen Kopf festzuhalten.")
    digest = bytes.fromhex(current.hash)
    token, stamped_at, problem = None, None, None
    try:
        token = timestamp.request(digest)
        stamped_at = timestamp.check(token, digest)
    except (timestamp.TimestampError, ImproperlyConfigured) as error:
        token, problem = None, f"Kein Zeitstempel: {error}"
    witness = WitnessHead.objects.create(
        seq=current.seq, head_hash=current.hash, token=token, stamped_at=stamped_at
    )
    return witness, problem


def report(witness: WitnessHead) -> str:
    """Text für Mail und Datei: nur Nummer, Hash und Zeitstempel."""
    at = timezone.localtime(witness.at)
    lines = [
        f"PSAgent – Kettenkopf vom {at:%d.%m.%Y %H:%M}",
        "",
        f"Verein: {settings.PSAGENT_CLUB_NAME or '–'}",
        f"Kettenkopf: Nummer {witness.seq}",
        f"Hash (SHA-256): {witness.head_hash}",
        f"Vorgänge in der Kette bis hier: {witness.seq}",
    ]
    if witness.token is None:
        lines.append("Zeitstempel (RFC 3161): fehlt heute; der nächste Lauf holt ihn für einen")
        lines.append("neueren Kopf, der alles davor mit abdeckt.")
    else:
        assert witness.stamped_at is not None  # noqa: S101 – Datenbankregel: Token nur mit Zeit
        lines.append(f"Zeitstempel (RFC 3161): {witness.stamped_at:%d.%m.%Y %H:%M:%S} UTC")
    lines += [
        "",
        "Diese Nachricht bitte unverändert aufbewahren. Sie belegt den Stand der Hash-Kette",
        "unabhängig vom PSAgent-Server.",
        "",
        "Prüfen in PSAgent (Admin):",
        f"  uv run python manage.py kette_pruefen --kopf {witness.seq}:{witness.head_hash}",
    ]
    if witness.token is not None:
        encoded = base64.b64encode(bytes(witness.token)).decode()
        lines += [
            "",
            "Zeitstempel unabhängig prüfen (OpenSSL): den Block unten als kettenkopf.b64",
            "speichern, dann",
            "  base64 -d kettenkopf.b64 > kettenkopf.tst",
            f"  openssl ts -verify -digest {witness.head_hash} -token_in -in kettenkopf.tst \\",
            "    -CAfile <Zertifikat der Zertifizierungsstelle des Zeitstempeldienstes>",
            "",
            *textwrap.wrap(encoded, 64),
        ]
    return "\n".join(lines) + "\n"


def deliver(witness: WitnessHead) -> list[str]:
    """Datei schreiben und Mail senden; gibt die Fehler zurück (leer = alles zugestellt)."""
    text, errors = report(witness), []
    folder = str(settings.PSAGENT_WITNESS_DIR or "").strip()
    if folder:
        name = f"kettenkopf-{timezone.localtime(witness.at):%Y-%m-%d}-{witness.seq}.txt"
        try:
            path = Path(folder) / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        except OSError as error:
            errors.append(f"Datei nicht geschrieben ({type(error).__name__}).")
    recipients = list(settings.PSAGENT_WITNESS_TO)
    if not recipients:
        errors.append("Kein Zeugen-Postfach eingetragen (PSAGENT_WITNESS_TO).")
    else:
        at = timezone.localtime(witness.at)
        message = EmailMessage(
            subject=f"PSAgent Kettenkopf {at:%d.%m.%Y}: Nummer {witness.seq}",
            body=text,
            to=recipients,
        )
        try:
            message.send()
        except Exception as error:
            errors.append(f"Zeugen-Mail nicht gesendet ({type(error).__name__}).")
    return errors


def known_heads() -> list[chain.Head]:
    """Alle festgehaltenen Köpfe; sie müssen weiter in der Kette stehen."""
    return [chain.Head(w.seq, w.head_hash) for w in WitnessHead.objects.order_by("seq", "pk")]


def verify() -> list[str]:
    """Prüft die Zeitstempel der festgehaltenen Köpfe und ob sie aktuell sind."""
    problems = []
    for witness in WitnessHead.objects.exclude(token=None).order_by("seq", "pk"):
        assert witness.token is not None  # noqa: S101 – oben gefiltert
        try:
            stamped = timestamp.check(bytes(witness.token), bytes.fromhex(witness.head_hash))
        except ImproperlyConfigured as error:
            return [f"Zeitstempel nicht prüfbar: {error}"]
        except timestamp.TimestampError as error:
            problems.append(f"Kettenkopf {witness.seq}: {error}")
            continue
        if stamped != witness.stamped_at:
            problems.append(f"Kettenkopf {witness.seq}: Zeit weicht vom Zeitstempel ab.")
    if str(settings.PSAGENT_TSA_URL or "").strip():
        problems.extend(_freshness())
    return problems


def _freshness() -> list[str]:
    limit = timezone.now() - MAX_GAP
    if not ChainLink.objects.filter(at__lt=limit).exists():
        return []
    latest = WitnessHead.objects.exclude(token=None).order_by("-stamped_at").first()
    if latest is None:
        return ["Für die Kette gibt es keinen Zeitstempel."]
    assert latest.stamped_at is not None  # noqa: S101 – Datenbankregel: Token nur mit Zeit
    if latest.stamped_at < limit:
        return [f"Seit {timezone.localtime(latest.stamped_at):%d.%m.%Y} kein gültiger Zeitstempel."]
    return []
