"""Tägliche Sammelmail zu neuen Sperren an Vereinsadressen (#141).

Festlegung (Felix, 04.11.2026):

- Einmal täglich, nur wenn es neue Sperren gab. Jede Sperre zählt: am PC, an der Theke („Schaden
  melden“, Rückgabe mit Schaden) und aus einer PSA-Prüfung.
- Eigene Empfängerliste, getrennt von der Sammelmail zu überfälligen Ausleihen (#128), z. B. das
  Postfach der PSA-Sachkundigen. Der Admin schaltet die Mail und pflegt die Empfänger (jede
  Änderung mit YubiKey, in der Ansicht geprüft) und kann eine Testmail senden.
- Inhalt je Sperre: Teil, EI, Zeitpunkt, wer gesperrt hat, Kommentar (an der Theke mit Grund),
  Stand jetzt und Link zur Teileseite. Keine Daten des Entleihers.

„Neu“ heißt: nach der jüngsten Sperre der letzten gesendeten Mail erfasst
(`LockDigestRun.last_event`), auch wenn der Zeitpunkt zurückliegt (Nacherfassung nach dem
Notbetrieb). Vor der ersten Mail zählen Sperren ab dem ersten Einschalten. Scheitert der Versand,
wird nichts gespeichert; der nächste Lauf nennt dieselben Sperren noch einmal.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime

from django.conf import settings
from django.db.models import QuerySet
from django.template.loader import render_to_string
from django.utils import timezone

from accounts.models import User
from accounts.rights import Right, require
from inventory.models import EventKind, LockDigestRun, LockDigestSetting, PartEvent
from inventory.timeline import actor_name
from mailer.digests import DigestError, absolute_link, clean_recipients, send_each

SETTINGS_URL = "inventory:lock_digest_settings"


@dataclass(frozen=True)
class Item:
    """Eine neue Sperre, wie sie in der Mail steht."""

    event: PartEvent
    name: str
    eis: str
    actor: str
    comment: str
    from_inspection: bool
    status: str
    link: str


@dataclass(frozen=True)
class Result:
    sent: bool
    locks: int = 0
    recipients: int = 0
    reason: str = ""


def current() -> LockDigestSetting | None:
    return LockDigestSetting.objects.first()


def recipients() -> list[str]:
    setting = current()
    return list(setting.recipients) if setting else []


def enabled() -> bool:
    setting = current()
    return bool(setting and setting.enabled and setting.recipients)


def configure(on: bool, addresses: Sequence[str], *, actor: User | None) -> LockDigestSetting:
    """Neuer Stand von Schalter und Empfängern; nur Admin (die Ansicht verlangt den YubiKey)."""
    require(actor, Right.LOCK_DIGEST)
    cleaned = clean_recipients(addresses)
    if on and not cleaned:
        raise DigestError("Mindestens eine Adresse eintragen, sonst lässt sich nichts senden.")
    return LockDigestSetting.objects.create(enabled=on, recipients=cleaned, actor=actor)


def _last_run() -> LockDigestRun | None:
    return LockDigestRun.objects.order_by("-last_event_id", "-id").first()


def since() -> datetime | None:
    """Ab wann Sperren als neu gelten: letzte Mail, sonst erstes Einschalten, sonst nie."""
    last = _last_run()
    if last is not None:
        return last.sent_at
    first_on = LockDigestSetting.objects.filter(enabled=True).order_by("at", "id").first()
    return first_on.at if first_on else None


def _new_events() -> QuerySet[PartEvent]:
    events = PartEvent.objects.filter(kind=EventKind.LOCKED)
    last = _last_run()
    if last is not None:
        return events.filter(pk__gt=last.last_event_id)
    start = since()
    return events.filter(at__gte=start) if start else events.none()


def new_locks() -> list[Item]:
    """Sperren seit der letzten Mail, in der Reihenfolge ihrer Erfassung."""
    events = (
        _new_events()
        .select_related("part", "actor")
        .prefetch_related("part__components", "part__events")
        .order_by("pk")
    )
    return [
        Item(
            event=event,
            name=event.part.name,
            eis=", ".join(str(c.ei) for c in event.part.components.all()),
            actor=actor_name(event.actor),
            comment=event.comment.strip().replace("\n", "\n    "),
            from_inspection=event.inspection_result_id is not None,
            status=event.part.status().label,
            link=absolute_link("inventory:part_detail", event.part_id),
        )
        for event in events
    ]


def _subject(count: int, on: date) -> str:
    noun = "neue Sperre" if count == 1 else "neue Sperren"
    return f"PSAgent: {count} {noun} ({on:%d.%m.%Y})"


def run(on: date | None = None, *, dry_run: bool = False) -> Result:
    """Täglicher Lauf. Sendet höchstens eine Sammelmail je Tag; im Probelauf nur zählen."""
    day = on or timezone.localdate()
    if not enabled():
        return Result(sent=False, reason="aus")
    if LockDigestRun.objects.filter(day=day).exists():
        return Result(sent=False, reason="heute schon gesendet")
    items = new_locks()
    addresses = recipients()
    if not items:
        return Result(sent=False, reason="keine neuen Sperren")
    if dry_run:
        return Result(sent=False, locks=len(items), recipients=len(addresses))
    context = {
        "since": since(),
        "club": settings.PSAGENT_CLUB_NAME,
        "items": items,
        "count_text": "1 Sperre" if len(items) == 1 else f"{len(items)} Sperren",
        "settings_link": absolute_link(SETTINGS_URL),
    }
    body = render_to_string("mail/sperren.txt", context)
    send_each(_subject(len(items), day), body, addresses)
    LockDigestRun.objects.create(
        day=day, last_event=items[-1].event, locks=len(items), recipients=len(addresses)
    )
    return Result(sent=True, locks=len(items), recipients=len(addresses))


def send_test(*, actor: User | None) -> int:
    """Testmail ohne Inhalt an die eingetragenen Empfänger; gibt deren Zahl zurück."""
    require(actor, Right.LOCK_DIGEST)
    addresses = recipients()
    if not addresses:
        raise DigestError("Es sind keine Empfänger eingetragen.")
    body = render_to_string(
        "mail/sperren_test.txt",
        {"club": settings.PSAGENT_CLUB_NAME, "settings_link": absolute_link(SETTINGS_URL)},
    )
    send_each("PSAgent: Testmail der Sammelmail zu neuen Sperren", body, addresses)
    return len(addresses)
