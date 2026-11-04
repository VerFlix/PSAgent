"""Hinweis vor dem Ablauf der Sachkunde (#26).

Festlegung (Felix, 04.11.2026): 60 Tage vor Ablauf ein Hinweis auf der Startseite, für die
Person selbst und für den Admin. Nach dem Ablauf bleibt der Hinweis stehen, solange die Person
eine Rolle ab PSA-Sachkundig hat. Eine anschließende Sachkunde (Verlängerung als neuer Eintrag,
lückenlos) schiebt das Ende hinaus; widerrufene Einträge zählen nicht. Ablauf und Verlängerung:
`docs/runbooks/sachkunde.md`.
"""

from dataclasses import dataclass
from datetime import date, timedelta

from django.utils import timezone

from accounts.models import Qualification, Role, User
from accounts.rights import Right, require

NOTICE_DAYS = 60


@dataclass(frozen=True)
class Expiry:
    user: User
    until: date  # letzter gültiger Tag
    days_left: int  # 0 = heute letzter Tag; negativ = abgelaufen

    @property
    def expired(self) -> bool:
        return self.days_left < 0


def _entries(user: User) -> list[Qualification]:
    return list(
        Qualification.objects.filter(user=user)
        .exclude(revocation__revoked_at__lte=timezone.now())
        .order_by("valid_from", "id")
    )


def qualified_until(user: User, today: date | None = None) -> date | None:
    """Letzter Tag lückenloser gültiger Sachkunde ab heute; None, wenn heute keine gilt."""
    day = today or timezone.localdate()
    entries = _entries(user)
    end: date | None = None
    while True:
        covering = [q.valid_until for q in entries if q.valid_from <= day <= q.valid_until]
        if not covering:
            return end
        end = max(covering)
        day = end + timedelta(days=1)


def expiry(user: User, today: date | None = None) -> Expiry | None:
    """Hinweis, wenn die Sachkunde in höchstens 60 Tagen endet oder (bei Rolle ab
    PSA-Sachkundig) schon abgelaufen ist; sonst None."""
    day = today or timezone.localdate()
    until = qualified_until(user, day)
    if until is not None:
        left = (until - day).days
        return Expiry(user, until, left) if left <= NOTICE_DAYS else None
    if not user.has_role(Role.PPE_EXPERT):
        return None
    past = [q.valid_until for q in _entries(user) if q.valid_until < day]
    if not past:
        return None
    last = max(past)
    return Expiry(user, last, (last - day).days)


def overview(actor: User | None, today: date | None = None) -> list[Expiry]:
    """Für den Admin: alle aktiven Konten mit Hinweis, abgelaufene zuerst."""
    require(actor, Right.MANAGE_USERS)
    found = [
        notice
        for user in User.objects.filter(is_active=True).order_by("last_name", "first_name")
        if (notice := expiry(user, today)) is not None
    ]
    return sorted(found, key=lambda notice: notice.days_left)
