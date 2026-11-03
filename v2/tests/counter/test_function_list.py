"""Abnahme #127: Jede Verleiher-Funktion ist an der Theke erreichbar (`docs/theke.md`).

Der Test liest die Rechtetabelle: Jede Zeile, die ein Verleiher nutzen darf (auch
eingeschränkt), muss in der Liste stehen. Ein neues Recht für Verleiher fällt so auf.
"""

from pathlib import Path

import pytest
from django.conf import settings
from django.http import HttpResponse
from django.test import Client
from django.urls import reverse

from accounts.devices import register_device
from accounts.models import Role
from accounts.rights import RIGHTS, Right, may_use
from tests.helpers import make_inspector

pytestmark = pytest.mark.django_db
DOC = Path(__file__).resolve().parents[2] / "docs" / "theke.md"


def lender_rights() -> list[Right]:
    lender = make_inspector(role=Role.LENDER, qualified=False)
    return [right for right in Right if may_use(lender, right)]


def test_every_lender_right_has_a_way_at_the_counter() -> None:
    text = DOC.read_text(encoding="utf-8")
    rows = [line for line in text.splitlines() if line.startswith("| ")]

    missing = [
        RIGHTS[right].label
        for right in lender_rights()
        if not any(line.startswith(f"| {RIGHTS[right].label} |") for line in rows)
    ]

    assert missing == []
    assert Right.LEND in lender_rights() and Right.MANAGE_PARTS not in lender_rights()


def test_counter_links_to_the_listed_ways(client: Client) -> None:
    """Die Startseite der Theke führt zu den Wegen der Liste."""
    client.force_login(make_inspector(role=Role.LENDER, qualified=False))

    page = client.get(reverse("counter:home")).content.decode()

    for name in (
        "counter:checkout_new",
        "counter:returns",
        "counter:damage_report",
        "counter:today",
        "lending:new_booking",
        "lending:borrower_create",
        "lending:calendar",
        "lending:overview",
        "inspections:reports",
        "emergency:index",
        "accounts:account",
    ):
        assert f'"{reverse(name)}"' in page, name


def test_normal_views_are_enlarged_on_the_counter_device(client: Client) -> None:
    """Festlegung: Die weiteren Punkte öffnen die normale Ansicht, für Touch vergrößert."""
    lender = make_inspector(role=Role.LENDER, qualified=False)
    client.force_login(lender)
    pc = client.get(reverse("lending:calendar")).content.decode()
    response = HttpResponse()
    register_device(
        "Tablet Ausgabe",
        actor=make_inspector(role=Role.ADMIN, qualified=False),
        response=response,
        counter=True,
    )
    cookie = settings.PSAGENT_CLUB_DEVICE_COOKIE
    client.cookies[cookie] = response.cookies[cookie].value

    tablet = client.get(reverse("lending:calendar")).content.decode()

    assert 'class="touch"' in tablet and 'class="touch"' not in pc
