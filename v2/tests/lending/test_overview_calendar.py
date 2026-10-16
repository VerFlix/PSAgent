"""Übersicht anstehender Ausleihen (Parität 18) und Kalender mit Verfügbarkeitssuche (20)."""

from datetime import date

import pytest
from django.test import Client
from django.urls import reverse

from accounts.models import Role, User
from lending.models import Borrower
from lending.services import create_borrower, lend, reserve
from tests.helpers import make_inspector
from tests.lending.conftest import DAY, TODAY, MakePart, checkout_kwargs

pytestmark = pytest.mark.django_db


def as_role(client: Client, role: Role) -> User:
    user = make_inspector(role=role, qualified=False)
    client.force_login(user)
    return user


def html(client: Client, url_name: str, **params: str) -> str:
    return client.get(reverse(url_name), params).content.decode()


# --- Parität 18: Übersicht ---


def test_parity_18_overview_groups_loans(
    client: Client, lender: User, make_part: MakePart, borrower: Borrower, monkeypatch: object
) -> None:
    overdue = make_part(designation="Helm A")
    due = make_part(designation="Helm B")
    later = make_part(designation="Helm C")
    upcoming = make_part(designation="Helm D")
    lend(borrower, [overdue], TODAY - 3 * DAY, TODAY - DAY, **checkout_kwargs(lender))
    lend(borrower, [due], TODAY, TODAY, **checkout_kwargs(lender))
    lend(borrower, [later], TODAY, TODAY + 5 * DAY, **checkout_kwargs(lender))
    reserve(
        create_borrower("Noa Probe"), [upcoming], TODAY + 2 * DAY, TODAY + 3 * DAY, actor=lender
    )
    client.force_login(lender)

    page = html(client, "lending:overview")

    sections = page.split("<h2>")
    assert "Überfällig (1)" in sections[1] and overdue.name in sections[1]
    assert "überfällig" in sections[1].split("</h2>", 1)[1]
    assert "Heute zurück (1)" in sections[2] and due.name in sections[2]
    assert "Ausgeliehen (1)" in sections[3] and later.name in sections[3]
    assert "Reserviert (1)" in sections[4] and "Noa Probe" in sections[4]


def test_parity_18_overview_is_empty_without_loans(client: Client, lender: User) -> None:
    client.force_login(lender)

    page = html(client, "lending:overview")

    assert "Nichts überfällig." in page and "Keine Reservierungen." in page


def test_viewer_has_no_overview(client: Client) -> None:
    """Verbotener Fall: Die Übersicht nennt Entleiher; Viewer sehen keine Personendaten."""
    as_role(client, Role.VIEWER)

    assert client.get(reverse("lending:overview")).status_code == 403


# --- Parität 20: Kalender ---


@pytest.fixture
def busy(make_part: MakePart, borrower: Borrower, lender: User) -> tuple[object, object]:
    helmet = make_part(designation="Helm", serial_number="SN-HELM")
    harness = make_part()
    lend(borrower, [helmet], TODAY, TODAY + 2 * DAY, **checkout_kwargs(lender))
    reserve(create_borrower("Noa Probe"), [harness], TODAY + 3 * DAY, TODAY + 3 * DAY, actor=lender)
    return helmet, harness


def test_parity_20_month_view_counts_days(client: Client, lender: User, busy: object) -> None:
    client.force_login(lender)

    page = html(client, "lending:calendar")

    assert "Oktober 2026" in page
    assert f"{TODAY:%d.%m.%Y}: 1 belegt" in page
    assert f"{TODAY + 3 * DAY:%d.%m.%Y}: 1 belegt" in page
    assert f"{TODAY + 4 * DAY:%d.%m.%Y}: 0 belegt" in page


def test_parity_20_week_view_lists_parts(client: Client, lender: User, busy: object) -> None:
    client.force_login(lender)

    page = html(client, "lending:calendar", ansicht="woche", datum=TODAY.isoformat())

    assert "Woche 12.10. – 18.10.2026" in page
    assert "Helm" in page and "Kim Beispiel" in page


def test_parity_20_day_list(client: Client, lender: User, busy: object) -> None:
    client.force_login(lender)

    page = html(client, "lending:calendar", datum=(TODAY + 3 * DAY).isoformat(), tag="1")

    day = page.split("Sonntag, 18.10.2026")[1]
    assert "Noa Probe" in day and "reserviert" in day


def test_parity_20_filter_by_designation_and_serial(
    client: Client, lender: User, busy: object
) -> None:
    client.force_login(lender)

    by_name = html(client, "lending:calendar", q="helm")
    by_serial = html(client, "lending:calendar", q="SN-HELM")

    for page in (by_name, by_serial):
        assert f"{TODAY:%d.%m.%Y}: 1 belegt" in page
        assert f"{TODAY + 3 * DAY:%d.%m.%Y}: 0 belegt" in page


def test_parity_20_filter_by_borrower_name(client: Client, lender: User, busy: object) -> None:
    client.force_login(lender)

    page = html(client, "lending:calendar", name="noa")

    assert f"{TODAY:%d.%m.%Y}: 0 belegt" in page
    assert f"{TODAY + 3 * DAY:%d.%m.%Y}: 1 belegt" in page


def test_parity_20_viewer_sees_calendar_without_names(client: Client, busy: object) -> None:
    """Viewer: Kalender ja, aber keine Namen und keine Suche nach Namen (Rechtetabelle)."""
    as_role(client, Role.VIEWER)

    week = html(client, "lending:calendar", ansicht="woche", datum=TODAY.isoformat())
    day = html(client, "lending:calendar", datum=TODAY.isoformat(), tag="1")
    filtered = html(client, "lending:calendar", name="noa")

    for page in (week, day):
        assert "Kim Beispiel" not in page and "Noa Probe" not in page
    assert "Entleiher (Name)" not in week
    assert f"{TODAY + 3 * DAY:%d.%m.%Y}: 1 belegt" in filtered  # Namensfilter wirkt nicht
    assert f"{TODAY:%d.%m.%Y}: 1 belegt" in filtered


def test_parity_20_availability_search(client: Client, busy: tuple[object, object]) -> None:
    as_role(client, Role.VIEWER)

    page = html(
        client,
        "lending:calendar",
        von=(TODAY + 3 * DAY).isoformat(),
        bis=(TODAY + 3 * DAY).isoformat(),
    )

    rows = page.split("Verfügbarkeit im Zeitraum")[1]
    assert "Helm" in rows and "frei" in rows
    assert "ist im Zeitraum bereits vergeben" in rows


def test_parity_20_availability_rejects_reversed_period(client: Client, lender: User) -> None:
    client.force_login(lender)

    page = html(client, "lending:calendar", von=TODAY.isoformat(), bis=(TODAY - DAY).isoformat())

    assert "„bis“ liegt vor „von“." in page


def test_parity_20_month_navigation_across_year(client: Client, lender: User) -> None:
    client.force_login(lender)

    page = html(client, "lending:calendar", datum=date(2026, 12, 10).isoformat())

    assert "Dezember 2026" in page
    assert "datum=2027-01-01" in page and "datum=2026-11-01" in page


def test_parity_20_invalid_date_falls_back_to_today(client: Client, lender: User) -> None:
    client.force_login(lender)

    page = html(client, "lending:calendar", datum="kein-datum", ansicht="jahr")

    assert "Oktober 2026" in page
