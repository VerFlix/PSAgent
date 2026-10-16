"""Rechtetabelle (Konzept, Abschnitt 5) an einer Stelle; geprüft wird im Server (#12).

Rollen bauen aufeinander auf: Admin > PSA-Sachkundig > Trainer/Materialwart > Verleiher >
Viewer. Manche Rechte hängen zusätzlich an einer gültigen Sachkunde, manche gelten für
niedrigere Rollen nur eingeschränkt („nur eigene“, „nur laufende Ausleihen“).

- `has_right(user, right, obj)` beantwortet die Frage für ein konkretes Objekt.
- `may_use(user, right)` sagt, ob jemand das Recht überhaupt (wenigstens eingeschränkt) hat;
  das prüfen die Ansichten vorab, die Einschränkung prüft die Ansicht bzw. der Dienst am Objekt.
- `require(actor, right, obj)` weist ab. `actor=None` steht für Vorgänge ohne angemeldete
  Person (Übernahme aus dem Altsystem, Verwaltungsbefehle); Ansichten übergeben immer die
  angemeldete Person.
- `requires(right)` und `public` kennzeichnen Ansichten; ein Test verlangt das für jede URL.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from functools import wraps
from typing import Any

from django.conf import settings
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse
from django.http.response import HttpResponseBase
from django.shortcuts import resolve_url

from accounts.models import ROLE_ORDER, Role, User


class Right(StrEnum):
    """Zeilen der Rechtetabelle, dazu „eigenes Konto“ für angemeldete Personen."""

    ACCOUNT = "account"
    VIEW_AVAILABILITY = "view_availability"
    VIEW_CONTACTS = "view_contacts"
    LEND = "lend"
    CHANGE_OTHERS_RESERVATIONS = "change_others_reservations"
    LOCK = "lock"
    BACKFILL_LOANS = "backfill_loans"
    BACKFILL_INSPECTIONS = "backfill_inspections"
    MANAGE_PARTS = "manage_parts"
    PPE_FIELDS = "ppe_fields"
    PPE_DOCUMENTS = "ppe_documents"
    EXPORT_PERSONAL = "export_personal"
    INSPECT = "inspect"
    CLARIFY = "clarify"
    MANAGE_USERS = "manage_users"
    MODIFY_SIGNED = "modify_signed"


def _active_loan(user: User, obj: object) -> bool:
    """Kontaktdaten nur, solange der Entleiher etwas ausgeliehen und noch nicht zurück hat.

    Eine Reservierung zählt bewusst nicht: Sonst könnte ein Verleiher die Kontaktdaten jedes
    Entleihers sehen, indem er auf dessen Namen etwas reserviert. Eine Ausgabe dagegen braucht
    Kurzkontrolle und Unterschrift vor Ort und steht mit Namen im Protokoll.
    """
    from lending.models import Booking, Borrower, Loan, LoanStatus

    if isinstance(obj, Borrower):
        return Loan.objects.filter(booking__borrower=obj, status=LoanStatus.LENT).exists()
    if isinstance(obj, Booking):
        return obj.loans.filter(status=LoanStatus.LENT).exists()
    if isinstance(obj, Loan):
        return obj.status == LoanStatus.LENT
    return False


def _own(user: User, obj: object) -> bool:
    """Eigene Vorgänge: angelegt bzw. erfasst von dieser Person."""
    for attribute in ("created_by_id", "actor_id", "inspector_id", "recorded_by_id"):
        if getattr(obj, attribute, None) is not None:
            return bool(getattr(obj, attribute) == user.pk)
    return False


@dataclass(frozen=True)
class Rule:
    """Eine Zeile: ab welcher Rolle, ob Sachkunde nötig ist, und Einschränkungen darunter."""

    label: str
    minimum: Role | None
    qualification: bool = False
    full_from: Role | None = None
    scope: Callable[[User, object], bool] | None = None
    scope_label: str = ""


RIGHTS: dict[Right, Rule] = {
    Right.ACCOUNT: Rule("Eigenes Konto und Startseite", Role.VIEWER),
    Right.VIEW_AVAILABILITY: Rule(
        "Verfügbarkeit und Kalender sehen (ohne Personendaten)", Role.VIEWER
    ),
    Right.VIEW_CONTACTS: Rule(
        "Kontaktdaten der Entleiher sehen",
        Role.LENDER,
        full_from=Role.PPE_EXPERT,
        scope=_active_loan,
        scope_label="nur laufende Ausleihen",
    ),
    Right.LEND: Rule("Reservieren, ausgeben, zurücknehmen", Role.LENDER),
    Right.CHANGE_OTHERS_RESERVATIONS: Rule(
        "Reservierungen anderer ändern oder stornieren",
        Role.LENDER,
        full_from=Role.TRAINER,
        scope=_own,
        scope_label="nur eigene",
    ),
    Right.LOCK: Rule("Sperren, jederzeit (mit Pflichtkommentar)", Role.LENDER),
    Right.BACKFILL_LOANS: Rule(
        "Ausleihen nacherfassen (Notbetrieb)",
        Role.LENDER,
        full_from=Role.PPE_EXPERT,
        scope=_own,
        scope_label="eigene",
    ),
    Right.BACKFILL_INSPECTIONS: Rule(
        "Prüfprotokolle nacherfassen (Notbetrieb)",
        Role.PPE_EXPERT,
        qualification=True,
        full_from=Role.ADMIN,
        scope=_own,
        scope_label="eigene",
    ),
    Right.MANAGE_PARTS: Rule(
        "Teile anlegen, Beschreibung und GAL pflegen, Einsatzdokumentation erzeugen",
        Role.TRAINER,
    ),
    Right.PPE_FIELDS: Rule(
        "PSA-relevante Felder ändern, „keine PSA“ setzen, neues Teil freigeben",
        Role.PPE_EXPERT,
        qualification=True,
    ),
    Right.PPE_DOCUMENTS: Rule(
        "PD und Haftungsausschluss erzeugen (mit eigenem Namen und Stempel)",
        Role.PPE_EXPERT,
        qualification=True,
    ),
    Right.EXPORT_PERSONAL: Rule("Export mit Personendaten", Role.PPE_EXPERT),
    Right.INSPECT: Rule(
        "Prüfungsrunde, entsperren, aussondern, nächste Prüfung setzen",
        Role.PPE_EXPERT,
        qualification=True,
    ),
    Right.CLARIFY: Rule("Klärungsliste nach dem Notbetrieb bearbeiten", Role.PPE_EXPERT),
    Right.MANAGE_USERS: Rule("Nutzer, Rollen, Sachkunde-Zertifikate verwalten", Role.ADMIN),
    Right.MODIFY_SIGNED: Rule("Signierte Einträge ändern oder löschen", None),
}


def _basic(user: object, rule: Rule, at: datetime | None) -> bool:
    if not isinstance(user, User) or not user.is_active or rule.minimum is None:
        return False
    if not user.has_role(rule.minimum):
        return False
    return not rule.qualification or user.has_valid_qualification(at)


def may_use(user: object, right: Right, *, at: datetime | None = None) -> bool:
    """Hat `user` das Recht überhaupt, ggf. nur eingeschränkt („nur eigene“ usw.)?"""
    return _basic(user, RIGHTS[right], at)


def has_right(
    user: object, right: Right, obj: object | None = None, *, at: datetime | None = None
) -> bool:
    """Darf `user` das Recht ausüben, bei eingeschränkten Rollen für genau dieses Objekt?"""
    rule = RIGHTS[right]
    if not _basic(user, rule, at):
        return False
    assert isinstance(user, User)  # noqa: S101 – von _basic geprüft
    if rule.scope is None or rule.full_from is None or user.has_role(rule.full_from):
        return True
    return obj is not None and rule.scope(user, obj)


def denial(right: Right) -> str:
    """Meldung für die Oberfläche, wenn ein Recht fehlt."""
    rule = RIGHTS[right]
    if rule.minimum is None:
        return f"„{rule.label}“ ist niemandem erlaubt."
    text = f"„{rule.label}“ ist erst ab der Rolle {rule.minimum.label} erlaubt"
    if rule.qualification:
        text += " und nur mit gültiger Sachkunde"
    if rule.scope_label:
        text += f"; darunter {rule.scope_label}"
    return text + "."


def require(
    actor: User | None, right: Right, obj: object | None = None, *, at: datetime | None = None
) -> None:
    """Weist ab, wenn `actor` das Recht (für `obj`) nicht hat. `None` = Import/Befehl."""
    if actor is not None and not has_right(actor, right, obj, at=at):
        raise PermissionDenied(denial(right))


def _login_required(request: HttpRequest) -> HttpResponse:
    """Nicht angemeldet: zur Anmeldung. Bei htmx-Teilanfragen lädt `HX-Redirect` die ganze Seite."""
    if request.headers.get("HX-Request") == "true":
        response = HttpResponse(status=401)
        response["HX-Redirect"] = resolve_url(settings.LOGIN_URL)
        return response
    return redirect_to_login(request.get_full_path())


def requires[V: Callable[..., HttpResponseBase]](right: Right) -> Callable[[V], V]:
    """Ansicht nur für angemeldete Personen mit `right` (wenigstens eingeschränkt).

    Gilt für ganze Seiten und htmx-Teilanfragen gleich. Eingeschränkte Rechte („nur eigene“)
    prüft die Ansicht bzw. der Dienst zusätzlich am Objekt mit `require`.
    """

    def decorator(view: V) -> V:
        @wraps(view)
        def protected(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponseBase:
            if not request.user.is_authenticated:
                return _login_required(request)
            if not may_use(request.user, right):
                raise PermissionDenied(denial(right))
            return view(request, *args, **kwargs)

        protected.psagent_right = right  # type: ignore[attr-defined]
        return protected  # type: ignore[return-value]

    return decorator


def public[V: Callable[..., HttpResponseBase]](view: V) -> V:
    """Kennzeichnet eine Ansicht, die bewusst ohne Anmeldung erreichbar ist."""
    view.psagent_public = True  # type: ignore[attr-defined]
    return view


def cell(right: Right, role: Role) -> str:
    """Eintrag der Rechtetabelle für eine Rolle, wie im Konzept geschrieben."""
    rule = RIGHTS[right]
    if rule.minimum is None:
        return "niemand"
    if ROLE_ORDER.index(role) < ROLE_ORDER.index(rule.minimum):
        return "–"
    restricted = rule.full_from is not None and ROLE_ORDER.index(role) < ROLE_ORDER.index(
        rule.full_from
    )
    text = rule.scope_label if restricted else "✓"
    return f"{text}, nur mit Sachkunde" if rule.qualification else text


def table_markdown() -> str:
    """Die Rechtetabelle als Markdown, von der höchsten zur niedrigsten Rolle (`docs/rechte.md`)."""
    roles = list(reversed(ROLE_ORDER))
    lines = [
        "| Recht | " + " | ".join(role.label for role in roles) + " |",
        "| --- |" + " --- |" * len(roles),
    ]
    for right, rule in RIGHTS.items():
        lines.append(f"| {rule.label} | " + " | ".join(cell(right, role) for role in roles) + " |")
    return "\n".join(lines) + "\n"
