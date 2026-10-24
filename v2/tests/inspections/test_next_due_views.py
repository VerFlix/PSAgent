"""Liste „Nächste Prüfungen“ und Zeitleiste je Teil (Parität 6)."""

from datetime import timedelta

import pytest
from django.test import Client
from django.urls import reverse

from accounts.models import Role
from inventory.models import EventKind
from inventory.services import create_product, link_document, record_event
from lending.services import create_borrower, lend, today
from tests.helpers import adopt_inspection, make_inspector
from tests.inspections.conftest import in_days

pytestmark = pytest.mark.django_db


def login(client: Client, role: Role = Role.VIEWER) -> None:
    client.force_login(make_inspector(role=role, qualified=False))


def page(client: Client, **params: str) -> str:
    return client.get(reverse("inspections:next_due"), params).content.decode()


def order(html: str, *names: str) -> list[str]:
    return sorted(names, key=html.index)


@pytest.fixture
def parts() -> dict[str, object]:
    soon = create_product({"designation": "Helm bald", "ei": "TST-N1"})
    adopt_inspection([soon], next_due=in_days(10))
    later = create_product({"designation": "Gurt später", "ei": "TST-N2"})
    adopt_inspection([later], next_due=in_days(200))
    overdue = create_product({"designation": "Seil überfällig", "ei": "TST-N3"})
    adopt_inspection([overdue], next_due=in_days(-5))
    locked = create_product({"designation": "Karabiner gesperrt", "ei": "TST-N4"})
    adopt_inspection([locked], next_due=in_days(300))
    record_event(locked, EventKind.LOCKED, actor=None, comment="Riss")
    new = create_product({"designation": "Schlinge neu", "ei": "TST-N5"})
    retired = create_product({"designation": "Alt ausgesondert", "ei": "TST-N6"})
    record_event(retired, EventKind.RETIRED, actor=None, comment="Alter")
    return {"soon": soon, "later": later, "overdue": overdue, "locked": locked, "new": new}


def test_parity_6_locked_first_then_new_then_by_due(client: Client, parts: object) -> None:
    login(client)

    html = page(client)

    assert order(
        html, "Karabiner gesperrt", "Schlinge neu", "Seil überfällig", "Helm bald", "Gurt später"
    ) == ["Karabiner gesperrt", "Schlinge neu", "Seil überfällig", "Helm bald", "Gurt später"]
    assert "Alt ausgesondert" not in html
    assert "seit 5 Tagen überfällig" in html
    assert "in 10 Tagen" in html
    assert "erste Prüfung offen" in html


def test_parity_6_quick_filter_30_days(client: Client, parts: object) -> None:
    login(client)

    html = page(client, schnell="30")

    assert "Helm bald" in html and "Seil überfällig" in html
    assert "Gurt später" not in html


def test_parity_6_quick_filter_overdue(client: Client, parts: object) -> None:
    login(client)

    html = page(client, schnell="ueberfaellig")

    assert "Seil überfällig" in html
    assert "Helm bald" not in html and "Gurt später" not in html


def test_parity_6_filter_by_date_status_and_search(client: Client, parts: object) -> None:
    login(client)

    by_date = page(client, bis=in_days(15).isoformat())
    by_status = page(client, status="locked")
    by_search = page(client, q="tst-n2")

    assert "Helm bald" in by_date and "Gurt später" not in by_date
    assert "Karabiner gesperrt" in by_status and "Helm bald" not in by_status
    assert "Gurt später" in by_search and "Helm bald" not in by_search


def test_parity_6_no_ppe_parts_are_listed_last(client: Client, parts: object) -> None:
    rope = create_product({"designation": "Bandschlinge ohne PSA", "ei": "TST-N7"})
    record_event(rope, EventKind.NO_PPE_SET, actor=None)
    login(client)

    html = page(client)

    assert order(html, "Gurt später", "Bandschlinge ohne PSA")[-1] == "Bandschlinge ohne PSA"
    assert "keine PSA" in html
    assert "Bandschlinge ohne PSA" not in page(client, schnell="30")


def test_parity_6_timeline_combines_events(client: Client) -> None:
    part = create_product({"designation": "Helm", "ei": "TST-Z1"})
    part.documents.add(link_document("https://example.org/gal.pdf", title="GAL"))
    adopt_inspection([part], next_due=in_days(100), comment="Sichtprüfung ok")
    lend(
        create_borrower("Kim Beispiel", address="Teststraße 1", email="kim@example.org"),
        [part],
        today(),
        today() + timedelta(days=1),
        actor=None,
        quick_check=True,
        gal_provided=True,
        signature=[[[1, 1], [2, 2]]],
        signer_name="Kim Beispiel",
    )
    record_event(part, EventKind.LOCKED, actor=None, comment="Kratzer")
    login(client, Role.LENDER)

    html = client.get(reverse("inventory:part_detail", args=[part.pk])).content.decode()

    timeline = html.split('id="zeitleiste"')[1]
    assert "PSA-Prüfung: in Ordnung" in timeline and "Sichtprüfung ok" in timeline
    assert "Verleih: ausgegeben" in timeline and "Kim Beispiel" in timeline
    assert "gesperrt" in timeline and "Kratzer" in timeline
    assert timeline.index("gesperrt") < timeline.index("Verleih: ausgegeben")  # neueste zuerst


def test_parity_6_timeline_hides_names_from_viewers(client: Client) -> None:
    part = create_product({"designation": "Helm", "ei": "TST-Z2"})
    part.documents.add(link_document("https://example.org/gal2.pdf", title="GAL"))
    adopt_inspection([part], next_due=in_days(100))
    lend(
        create_borrower("Kim Beispiel", address="Teststraße 1", email="kim@example.org"),
        [part],
        today(),
        today(),
        actor=None,
        quick_check=True,
        gal_provided=True,
        signature=[[[1, 1], [2, 2]]],
        signer_name="Kim Beispiel",
    )
    login(client, Role.VIEWER)

    html = client.get(reverse("inventory:part_detail", args=[part.pk])).content.decode()

    assert "Verleih: ausgegeben" in html
    assert "Kim Beispiel" not in html and "/verleih/" not in html.split('id="zeitleiste"')[1]


@pytest.mark.parametrize(("role", "shown"), [(Role.VIEWER, False), (Role.LENDER, True)])
def test_timeline_shows_who_locked_from_lender_on(client: Client, role: Role, shown: bool) -> None:
    """Felix, 24.10.2026: Viewer sehen in der Historie nicht, wer einen Vorgang erfasst hat."""
    part = create_product({"designation": "Helm", "ei": f"TST-Z3{role}"})
    staff = make_inspector()
    staff.last_name = "Sperrende"
    staff.save(update_fields=["last_name"])
    record_event(part, EventKind.LOCKED, actor=staff, comment="Kratzer")
    login(client, role)

    html = client.get(reverse("inventory:part_detail", args=[part.pk])).content.decode()
    timeline = html.split('id="zeitleiste"')[1]

    assert "gesperrt" in timeline and "Kratzer" in timeline
    assert ("Testperson Sperrende" in timeline) is shown


def test_parity_6_list_needs_login(client: Client) -> None:
    assert client.get(reverse("inspections:next_due")).status_code == 302
