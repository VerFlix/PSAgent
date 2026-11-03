"""Rückgabe und Schaden melden an der Theke (#127, Teil 2).

Festlegung (Felix, 31.10. und 03.11.2026): offene Ausleihen, überfällige zuerst; Ausleihe am
gescannten Teil erkennen; Teilrückgabe; Pflichten wie am PC (Parität 17). Schaden sperrt das
Teil: bei der Rückgabe reicht der Grund (bei „Sonstiges“ mit Kommentar), „Schaden melden“
verlangt Grund und Kommentar. Der Grund steht im Sperrkommentar. Abmelden nach jedem Vorgang.
"""

import json
from typing import Any

import pytest
from django.conf import settings
from django.http import HttpResponse
from django.test import Client
from django.urls import reverse

from accounts.devices import register_device
from accounts.models import Role, User
from counter.damage import REASONS, DamageError, lock_comment
from inventory.models import EventKind, Part, PartEvent
from inventory.services import record_event
from lending.models import Booking, Borrower, Loan, LoanEvent, LoanStatus
from lending.services import create_borrower, lend
from tests.counter.conftest import DAY, STROKES, TODAY, MakePart
from tests.helpers import make_inspector

pytestmark = pytest.mark.django_db

RETURNS = reverse("counter:returns")
DAMAGE = reverse("counter:damage_report")


def lent_out(borrower: Borrower, parts: list[Part], days: int, *, start_days: int = 0) -> Booking:
    start = TODAY + start_days * DAY
    return lend(
        borrower,
        parts,
        start,
        start + days * DAY,
        actor=None,
        quick_check=True,
        gal_provided=True,
        signature=STROKES,
        signer_name="Kim Beispiel",
    )


def return_url(booking: Booking) -> str:
    return reverse("counter:return_booking", args=[booking.pk])


def take_back(**changes: Any) -> dict[str, Any]:
    return {
        "zuruecknehmen": "1",
        "quick_check": "on",
        "signer_name": "Kim Beispiel",
        "signature": json.dumps(STROKES),
        **changes,
    }


def make_counter_device(client: Client) -> None:
    admin = make_inspector(role=Role.ADMIN, qualified=False)
    response = HttpResponse()
    register_device("Tablet Ausgabe", actor=admin, response=response, counter=True)
    cookie = settings.PSAGENT_CLUB_DEVICE_COOKIE
    client.cookies[cookie] = response.cookies[cookie].value


# --- Schadensgründe ---


def test_reasons_as_in_the_prototype() -> None:
    assert REASONS == (
        "Sichtbarer Schaden",
        "Sturz belastet",
        "Chemikalien / Hitze",
        "Unvollständig",
        "Abgelaufen",
        "Sonstiges",
    )


def test_lock_comment_rules() -> None:
    assert lock_comment("Sturz belastet", "", comment_required=False) == "Sturz belastet"
    assert lock_comment("Sturz belastet", " Kern sichtbar ", comment_required=False) == (
        "Sturz belastet: Kern sichtbar"
    )
    with pytest.raises(DamageError, match="beschreiben"):
        lock_comment("Sonstiges", "", comment_required=False)
    with pytest.raises(DamageError, match="beschreiben"):
        lock_comment("Sturz belastet", "", comment_required=True)
    with pytest.raises(DamageError, match="Grund"):
        lock_comment("", "irgendwas", comment_required=False)


# --- Liste und Erkennen am Teil ---


def test_open_loans_overdue_first(client_lender: Client, make_part: MakePart, lender: User) -> None:
    early = create_borrower("Jona Früh", address="Weg 1", email="jona@example.org")
    late = create_borrower("Erika Spät", address="Weg 2", email="erika@example.org")
    today = create_borrower("Paul Heute", address="Weg 3", email="paul@example.org")
    lent_out(early, [make_part("DAV-HL-H-1", designation="Helm")], 5)
    lent_out(late, [make_part("DAV-HL-H-2", designation="Helm")], 2, start_days=-5)
    lent_out(today, [make_part("DAV-HL-H-3", designation="Helm")], 0)

    page = client_lender.get(RETURNS).content.decode()

    assert page.index("Erika Spät") < page.index("Paul Heute") < page.index("Jona Früh")
    assert "3 Tage überfällig" in page and "fällig heute" in page and "vorzeitig" in page


def test_scanned_part_opens_its_loan(
    client_lender: Client, make_part: MakePart, borrower: Borrower
) -> None:
    helmet = make_part("DAV-HL-H-1", designation="Helm")
    booking = lent_out(borrower, [helmet, make_part("DAV-HL-G-1", size="M")], 7)
    loan = Loan.objects.get(part=helmet)

    response = client_lender.post(RETURNS, {"ei": "dav-hl-h-1"})

    assert response["Location"] == f"{return_url(booking)}?teil={loan.pk}"
    page = client_lender.get(response["Location"]).content.decode()
    assert f'name="zurueck" value="{loan.pk}" checked' in page


@pytest.mark.parametrize(
    ("ei", "message"), [("DAV-HL-H-9", "gibt es nicht"), ("DAV-HL-H-2", "nicht ausgeliehen")]
)
def test_scan_of_a_part_that_is_not_out(
    client_lender: Client, make_part: MakePart, ei: str, message: str
) -> None:
    make_part("DAV-HL-H-2", designation="Helm")

    page = client_lender.post(RETURNS, {"ei": ei}, follow=True).content.decode()

    assert message in page


# --- Rückgabe ---


def test_partial_return(
    client_lender: Client, make_part: MakePart, borrower: Borrower, lender: User
) -> None:
    helmet = make_part("DAV-HL-H-1", designation="Helm")
    belt = make_part("DAV-HL-G-1", size="M")
    booking = lent_out(borrower, [helmet, belt], 7)
    loan = Loan.objects.get(part=helmet)

    response = client_lender.post(return_url(booking), take_back(zurueck=[loan.pk]))

    assert response.status_code == 302 and response["Location"] == reverse("counter:home")
    assert Loan.objects.get(part=helmet).status == LoanStatus.RETURNED
    assert Loan.objects.get(part=belt).status == LoanStatus.LENT
    event = LoanEvent.objects.get(loan=loan, kind="returned")
    assert (event.actor, event.quick_check) == (lender, True)


def test_scan_on_the_return_page_marks_the_part_and_keeps_the_others(
    client_lender: Client, make_part: MakePart, borrower: Borrower
) -> None:
    helmet = make_part("DAV-HL-H-1", designation="Helm")
    belt = make_part("DAV-HL-G-1", size="M")
    booking = lent_out(borrower, [helmet, belt], 7)
    first, second = Loan.objects.get(part=helmet), Loan.objects.get(part=belt)

    page = client_lender.post(
        return_url(booking), {"zurueck": [first.pk], "ei": "DAV-HL-G-1"}
    ).content.decode()

    assert f'value="{first.pk}" checked' in page and f'value="{second.pk}" checked' in page
    assert Loan.objects.filter(status=LoanStatus.LENT).count() == 2  # noch nichts gespeichert


def test_scan_of_another_borrowers_part_is_refused(
    client_lender: Client, make_part: MakePart, borrower: Borrower
) -> None:
    booking = lent_out(borrower, [make_part("DAV-HL-H-1", designation="Helm")], 7)
    other = create_borrower("Noa Probe", address="Weg 9", email="noa@example.org")
    lent_out(other, [make_part("DAV-HL-H-2", designation="Helm")], 7)

    page = client_lender.post(return_url(booking), {"ei": "DAV-HL-H-2"}).content.decode()

    assert "gehört zur Ausleihe von Noa Probe" in page


def test_all_back(client_lender: Client, make_part: MakePart, borrower: Borrower) -> None:
    booking = lent_out(
        borrower,
        [make_part("DAV-HL-H-1", designation="Helm"), make_part("DAV-HL-G-1", size="L")],
        7,
    )

    page = client_lender.post(return_url(booking), {"alle": "1"}).content.decode()

    for loan in booking.loans.all():
        assert f'name="zurueck" value="{loan.pk}" checked' in page


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"quick_check": ""}, "Kurzprüfung"),
        ({"signature": ""}, "unterschreiben"),
        ({"zurueck": []}, "mindestens ein"),
    ],
)
def test_return_duties_as_at_the_pc(
    client_lender: Client,
    make_part: MakePart,
    borrower: Borrower,
    changes: dict[str, Any],
    message: str,
) -> None:
    helmet = make_part("DAV-HL-H-1", designation="Helm")
    booking = lent_out(borrower, [helmet], 7)
    data = {"zurueck": [Loan.objects.get(part=helmet).pk], **take_back(), **changes}

    response = client_lender.post(return_url(booking), data)

    assert response.status_code == 400 and message in response.content.decode()
    assert Loan.objects.get(part=helmet).status == LoanStatus.LENT


def test_damage_on_return_locks_with_the_reason(
    client_lender: Client, make_part: MakePart, borrower: Borrower, lender: User
) -> None:
    helmet = make_part("DAV-HL-H-1", designation="Helm")
    belt = make_part("DAV-HL-G-1", size="M")
    booking = lent_out(borrower, [helmet, belt], 7)
    first, second = Loan.objects.get(part=helmet), Loan.objects.get(part=belt)

    response = client_lender.post(
        return_url(booking),
        take_back(zurueck=[first.pk, second.pk], schaden=[first.pk], reason="Sturz belastet"),
        follow=True,
    )

    assert "Zurückgenommen: 2 Teile, davon 1 gesperrt." in response.content.decode()
    helmet.refresh_from_db()
    assert helmet.is_locked() and not belt.is_locked()
    event = PartEvent.objects.get(part=helmet, kind=EventKind.LOCKED)
    assert (event.comment, event.actor) == ("Sturz belastet", lender)


def test_damage_alone_counts_as_back(
    client_lender: Client, make_part: MakePart, borrower: Borrower
) -> None:
    """Nur „Schaden“ angekreuzt: Das Teil ist da, also zurück und gesperrt."""
    helmet = make_part("DAV-HL-H-1", designation="Helm")
    booking = lent_out(borrower, [helmet], 7)
    loan = Loan.objects.get(part=helmet)

    client_lender.post(return_url(booking), take_back(schaden=[loan.pk], reason="Unvollständig"))

    assert Loan.objects.get(part=helmet).status == LoanStatus.RETURNED
    assert helmet.is_locked()


@pytest.mark.parametrize(
    ("reason", "comment", "message"),
    [("", "", "Grund antippen"), ("Sonstiges", "", "beschreiben")],
)
def test_damage_on_return_needs_a_reason(
    client_lender: Client,
    make_part: MakePart,
    borrower: Borrower,
    reason: str,
    comment: str,
    message: str,
) -> None:
    helmet = make_part("DAV-HL-H-1", designation="Helm")
    booking = lent_out(borrower, [helmet], 7)
    loan = Loan.objects.get(part=helmet)

    response = client_lender.post(
        return_url(booking),
        take_back(zurueck=[loan.pk], schaden=[loan.pk], reason=reason, comment=comment),
    )

    assert response.status_code == 400 and message in response.content.decode()
    assert Loan.objects.get(part=helmet).status == LoanStatus.LENT and not helmet.is_locked()


def test_counter_device_logs_out_after_the_return(
    client: Client, lender: User, make_part: MakePart, borrower: Borrower
) -> None:
    make_counter_device(client)
    client.force_login(lender)
    helmet = make_part("DAV-HL-H-1", designation="Helm")
    booking = lent_out(borrower, [helmet], 7)

    response = client.post(
        return_url(booking), take_back(zurueck=[Loan.objects.get(part=helmet).pk]), follow=True
    )

    assert response.redirect_chain[0][0] == reverse("accounts:login")
    assert "_auth_user_id" not in client.session
    assert "Zurückgenommen: 1 Teil. Abgemeldet." in response.content.decode()


def test_viewer_cannot_return(client: Client, make_part: MakePart, borrower: Borrower) -> None:
    helmet = make_part("DAV-HL-H-1", designation="Helm")
    booking = lent_out(borrower, [helmet], 7)
    client.force_login(make_inspector(role=Role.VIEWER, qualified=False))

    response = client.post(
        return_url(booking), take_back(zurueck=[Loan.objects.get(part=helmet).pk])
    )

    assert response.status_code == 403
    assert Loan.objects.get(part=helmet).status == LoanStatus.LENT


# --- Schaden melden ---


def test_report_damage_locks_the_part(
    client_lender: Client, make_part: MakePart, lender: User
) -> None:
    rope = make_part("DAV-HL-S-21", designation="Einfachseil 50 m")

    chosen = client_lender.post(DAMAGE, {"ei": "DAV-HL-S-21"}).content.decode()
    assert "DAV-HL-S-21 Einfachseil 50 m" in chosen
    response = client_lender.post(
        DAMAGE,
        {
            "sperren": "1",
            "teil": rope.pk,
            "teil_ei": "DAV-HL-S-21",
            "reason": "Chemikalien / Hitze",
            "comment": "Mantel verfärbt",
        },
        follow=True,
    )

    assert "Gesperrt: Einfachseil 50 m DAV-HL-S-21." in response.content.decode()
    event = PartEvent.objects.get(part=rope, kind=EventKind.LOCKED)
    assert (event.comment, event.actor) == ("Chemikalien / Hitze: Mantel verfärbt", lender)


@pytest.mark.parametrize(
    ("reason", "comment", "message"),
    [("", "Mantel verfärbt", "Grund antippen"), ("Sturz belastet", "", "beschreiben")],
)
def test_report_damage_needs_reason_and_comment(
    client_lender: Client, make_part: MakePart, reason: str, comment: str, message: str
) -> None:
    rope = make_part("DAV-HL-S-21", designation="Einfachseil 50 m")

    response = client_lender.post(
        DAMAGE, {"sperren": "1", "teil": rope.pk, "reason": reason, "comment": comment}
    )

    assert response.status_code == 400 and message in response.content.decode()
    assert not rope.is_locked()


def test_report_damage_on_a_locked_or_lent_part(
    client_lender: Client, make_part: MakePart, borrower: Borrower, lender: User
) -> None:
    locked = make_part("DAV-HL-H-1", designation="Helm")
    record_event(locked, EventKind.LOCKED, actor=lender, comment="Riss")
    lent = make_part("DAV-HL-H-2", designation="Helm")
    lent_out(borrower, [lent], 7)

    already = client_lender.post(DAMAGE, {"ei": "DAV-HL-H-1"}).content.decode()
    out = client_lender.post(DAMAGE, {"ei": "DAV-HL-H-2"}).content.decode()

    assert "bereits gesperrt" in already and 'name="sperren"' not in already
    assert "gerade verliehen" in out and 'name="sperren"' in out


def test_report_damage_unknown_ei(client_lender: Client) -> None:
    page = client_lender.post(DAMAGE, {"ei": "DAV-HL-X-1"}).content.decode()

    assert "DAV-HL-X-1 gibt es nicht." in page


def test_viewer_cannot_report_damage(client: Client, make_part: MakePart) -> None:
    rope = make_part("DAV-HL-S-21", designation="Einfachseil 50 m")
    client.force_login(make_inspector(role=Role.VIEWER, qualified=False))

    response = client.post(
        DAMAGE, {"sperren": "1", "teil": rope.pk, "reason": "Sonstiges", "comment": "x"}
    )

    assert response.status_code == 403 and not rope.is_locked()


def test_suggestions_for_return_and_damage(
    client_lender: Client, make_part: MakePart, borrower: Borrower, lender: User
) -> None:
    lent_out(borrower, [make_part("DAV-HL-H-1", designation="Helm")], 7)
    locked = make_part("DAV-HL-H-10", designation="Helm")
    record_event(locked, EventKind.LOCKED, actor=lender, comment="Riss")
    url = reverse("counter:part_suggestions")

    back = client_lender.get(url, {"ei": "DAV-HL-H-1", "fuer": "rueckgabe"}).content.decode()
    damage = client_lender.get(url, {"ei": "DAV-HL-H-1", "fuer": "schaden"}).content.decode()

    assert f"fällig {TODAY + 7 * DAY:%d.%m.%Y}" in back and "ist nicht ausgeliehen" in back
    assert "ist bereits gesperrt" in damage
