"""Archiv ausgesonderter Teile (Parität 10, #40).

Festlegungen (Felix, 22.10.2026): eigene Seite „Archiv“ unter Teile, für jede Rolle. Sie zeigt
Datum, Grund und Teil; wer ausgesondert hat, sehen Rollen ab Verleiher. Historie und
Haftungsausschluss stehen auf der Teileseite. Im Altsystem lag das Archiv in einer zweiten
Datenbankdatei; in PSAgent 2.0 ist es der Status „ausgesondert“ (nichts wird gelöscht).
"""

from datetime import timedelta

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role, User
from inventory.models import EventKind, Part
from inventory.services import record_event
from tests.helpers import make_inspector
from tests.inventory.conftest import MakeProduct

pytestmark = pytest.mark.django_db
ARCHIVE = reverse("inventory:part_archive")


@pytest.fixture
def expert(db: None) -> User:
    user = make_inspector()
    user.last_name = "Aussonderer"
    user.save(update_fields=["last_name"])
    return user


def retire(part: Part, actor: User | None, reason: str, days_ago: int = 0) -> None:
    record_event(
        part,
        EventKind.RETIRED,
        actor=actor,
        comment=reason,
        at=timezone.now() - timedelta(days=days_ago),
    )


def page(client: Client, role: Role, **query: str) -> str:
    client.force_login(make_inspector(role=role, qualified=False))
    return client.get(ARCHIVE, query).content.decode()


def test_parity_10_archive_lists_retired_parts_with_date_and_reason(
    client: Client, make_product: MakeProduct, expert: User
) -> None:
    gone = make_product("TST-ARC1")
    active = make_product("TST-ARC2")
    retire(gone, expert, "Naht beschädigt", days_ago=3)

    text = page(client, Role.VIEWER)

    assert "TST-ARC1" in text and "Naht beschädigt" in text
    assert f"{timezone.localdate() - timedelta(days=3):%d.%m.%Y}" in text
    assert reverse("inventory:part_detail", args=[gone.pk]) in text
    assert "TST-ARC2" not in text and reverse("inventory:part_detail", args=[active.pk]) not in text


@pytest.mark.parametrize(
    ("role", "shown"),
    [
        (Role.VIEWER, False),
        (Role.LENDER, True),
        (Role.TRAINER, True),
        (Role.PPE_EXPERT, True),
        (Role.ADMIN, True),
    ],
)
def test_who_retired_is_shown_from_lender_on(
    role: Role, shown: bool, client: Client, make_product: MakeProduct, expert: User
) -> None:
    retire(make_product("TST-ARC3"), expert, "Sturz")

    assert ("Testperson Aussonderer" in page(client, role)) is shown


def test_legacy_retirement_shows_legacy_system(client: Client, make_product: MakeProduct) -> None:
    retire(make_product("TST-ARC4"), None, "aus dem Altsystem-Archiv")

    assert "Altsystem" in page(client, Role.LENDER)


def test_newest_first_and_search_by_ei(
    client: Client, make_product: MakeProduct, expert: User
) -> None:
    retire(make_product("TST-ARC5"), expert, "alt", days_ago=30)
    retire(make_product("TST-ARC6"), expert, "neu", days_ago=1)

    text = page(client, Role.VIEWER)
    found = page(client, Role.VIEWER, q="tst - arc5")

    assert text.index("TST-ARC6") < text.index("TST-ARC5")
    assert "TST-ARC5" in found and "TST-ARC6" not in found


def test_empty_archive(client: Client, make_product: MakeProduct) -> None:
    make_product("TST-ARC7")

    assert "Keine ausgesonderten Teile" in page(client, Role.VIEWER)


def test_parity_10_retired_part_keeps_history_and_disclaimer(
    client: Client, make_product: MakeProduct, expert: User
) -> None:
    """Von der Archivzeile zur Teileseite: Historie mit Aussonderung, Haftungsausschluss."""
    part = make_product("TST-ARC8")
    retire(part, expert, "Ablegereife erreicht")
    client.force_login(expert)

    detail = client.get(reverse("inventory:part_detail", args=[part.pk])).content.decode()
    disclaimer = client.get(reverse("inventory:part_disclaimer", args=[part.pk]))

    assert "ausgesondert" in detail and "Ablegereife erreicht" in detail
    assert reverse("inventory:part_disclaimer", args=[part.pk]) in detail
    assert disclaimer.status_code == 200
    assert disclaimer["Content-Type"] == "application/pdf"


def test_part_list_links_to_the_archive(client: Client) -> None:
    client.force_login(make_inspector(role=Role.VIEWER, qualified=False))

    assert ARCHIVE in client.get(reverse("inventory:part_list")).content.decode()
