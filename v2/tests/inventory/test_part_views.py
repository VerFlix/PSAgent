"""Stammdaten im Browser: anlegen (Parität 1, 2), Liste, Detail, Sperren (#31, #32).

Dass Viewer und Verleiher keine Teile anlegen und nur Sachkundige entsperren, prüft
`tests/test_url_rights.py` für jede URL; hier stehen die fachlichen Fälle.
"""

from typing import Any

import pytest
from django.test import Client
from django.urls import reverse

from accounts.models import Role, User
from inventory.models import Component, Document, EventKind, Part, PartKind, PartStatus
from inventory.services import create_product, record_event
from tests.helpers import adopt_inspection, make_inspector
from tests.inventory.conftest import MakeProduct

pytestmark = pytest.mark.django_db
PDF = b"%PDF-1.4 kuenstliche GAL"


@pytest.fixture
def trainer(client: Client) -> User:
    user = make_inspector(role=Role.TRAINER, qualified=False)
    client.force_login(user)
    return user


def product_data(ei: str = "TST-P1", **extra: Any) -> dict[str, Any]:
    return {
        "name": "",
        "bauteil-designation": "Klettergurt",
        "bauteil-standard": "EN 12277",
        "bauteil-model_name": "Testmodell A1",
        "bauteil-manufacturer": "Beispiel Bergsport",
        "bauteil-year_of_manufacture": "2024",
        "bauteil-purchase_date": "2024-03-01",
        "bauteil-first_use_date": "",
        "bauteil-ei": ei,
        "bauteil-serial_number": "SN-1",
        "gal_url": "",
        "gal_title": "",
        **extra,
    }


def system_data(*eis: str, rows: int = 3, **extra: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "name": "Klettersteigset Test",
        "bauteile-TOTAL_FORMS": str(rows),
        "bauteile-INITIAL_FORMS": "0",
        "bauteile-MIN_NUM_FORMS": "0",
        "bauteile-MAX_NUM_FORMS": "30",
        "gal_url": "",
        "gal_title": "",
        **extra,
    }
    for index, ei in enumerate(eis):
        data[f"bauteile-{index}-designation"] = f"Bauteil {index + 1}"
        data[f"bauteile-{index}-ei"] = ei
    return data


# --- Parität 1: Produkt anlegen ---


def test_parity_1_create_product_with_gal_link(client: Client, trainer: User) -> None:
    response = client.post(
        reverse("inventory:product_create"),
        product_data(gal_url="https://example.org/gal/a1.pdf", gal_title="GAL A1"),
    )

    part = Part.objects.get()
    assert response["Location"] == reverse("inventory:part_detail", args=[part.pk])
    assert (part.kind, part.name, part.created_by) == (
        PartKind.PRODUCT,
        "Klettergurt Testmodell A1",
        trainer,
    )
    assert part.components.get().year_of_manufacture == 2024
    assert part.status() == PartStatus.NEW  # erst nach der ersten PSA-Prüfung verleihbar
    assert part.documents.get().url == "https://example.org/gal/a1.pdf"


def test_parity_1_gal_pdf_is_stored_once(client: Client, trainer: User) -> None:
    for ei in ("TST-P1", "TST-P2"):
        upload = _upload()
        client.post(reverse("inventory:product_create"), {**product_data(ei), "gal_file": upload})

    assert Part.objects.count() == 2
    assert Document.objects.count() == 1


def _upload(content: bytes = PDF, name: str = "gal.pdf") -> Any:
    from django.core.files.uploadedfile import SimpleUploadedFile

    return SimpleUploadedFile(name, content, content_type="application/pdf")


def test_parity_1_non_pdf_rolls_back_everything(client: Client, trainer: User) -> None:
    response = client.post(
        reverse("inventory:product_create"),
        {**product_data(), "gal_file": _upload(b"<html>", "gal.pdf")},
    )

    assert response.status_code == 200
    assert "kein PDF" in response.content.decode()
    assert not Part.objects.exists()
    assert not Component.objects.exists()


@pytest.mark.parametrize("variant", ["TST-P1", "tst-p1", " TST-P1 ", "Tst- P 1"])
def test_parity_1_ei_is_unique_ignoring_case_and_spaces(
    variant: str, client: Client, trainer: User
) -> None:
    create_product({"designation": "Helm", "ei": "TST-P1"})

    response = client.post(reverse("inventory:product_create"), product_data(variant))

    assert response.status_code == 200
    assert "ist schon vergeben" in response.content.decode()
    assert Part.objects.count() == 1


def test_parity_1_ei_is_required(client: Client, trainer: User) -> None:
    response = client.post(reverse("inventory:product_create"), product_data("   "))

    assert response.status_code == 200
    assert not Part.objects.exists()


def test_parity_1_no_ppe_needs_qualification(client: Client, trainer: User) -> None:
    """„keine PSA“ setzen nur mit Sachkunde: Ein Trainer bekommt eine Meldung, nichts entsteht."""
    response = client.post(reverse("inventory:product_create"), product_data(no_ppe="on"))

    assert response.status_code == 200
    assert "nur mit gültiger Sachkunde" in response.content.decode()
    assert not Part.objects.exists()


def test_parity_1_expert_sets_no_ppe(client: Client) -> None:
    client.force_login(make_inspector())

    client.post(reverse("inventory:product_create"), product_data(no_ppe="on"))

    part = Part.objects.get()
    assert part.has_no_ppe()
    assert part.status() == PartStatus.RELEASED


def test_parity_1_name_can_be_given(client: Client, trainer: User) -> None:
    client.post(reverse("inventory:product_create"), product_data(name="Gurt rot"))

    assert Part.objects.get().name == "Gurt rot"


# --- Parität 2: System anlegen ---


def test_parity_2_system_with_more_than_three_components(client: Client, trainer: User) -> None:
    eis = [f"TST-S{i}" for i in range(1, 6)]

    client.post(reverse("inventory:system_create"), system_data(*eis, rows=5))

    part = Part.objects.get()
    assert part.kind == PartKind.SYSTEM
    assert list(part.components.order_by("position").values_list("ei", flat=True)) == eis


def test_parity_2_empty_rows_are_ignored(client: Client, trainer: User) -> None:
    client.post(reverse("inventory:system_create"), system_data("TST-S1"))

    assert Part.objects.get().components.count() == 1


def test_parity_2_system_needs_a_component(client: Client, trainer: User) -> None:
    response = client.post(reverse("inventory:system_create"), system_data())

    assert "mindestens ein Bauteil" in response.content.decode()
    assert not Part.objects.exists()


def test_parity_2_same_ei_twice_in_one_system(client: Client, trainer: User) -> None:
    response = client.post(reverse("inventory:system_create"), system_data("TST-S1", " tst-s 1"))

    assert "dieselbe EI" in response.content.decode()
    assert not Part.objects.exists()


def test_parity_2_component_row_via_htmx(client: Client, trainer: User) -> None:
    response = client.get(
        reverse("inventory:system_component_row"),
        {"bauteile-TOTAL_FORMS": "3"},
        headers={"HX-Request": "true"},
    )

    html = response.content.decode()
    assert 'name="bauteile-3-ei"' in html
    assert 'name="bauteile-TOTAL_FORMS" value="4"' in html
    assert 'hx-swap-oob="true"' in html


@pytest.mark.parametrize("value", ["abc", "-5", "999"])
def test_component_row_index_is_bounded(value: str, client: Client, trainer: User) -> None:
    html = client.get(
        reverse("inventory:system_component_row"), {"bauteile-TOTAL_FORMS": value}
    ).content.decode()

    assert 'name="bauteile-' in html
    assert "bauteile-30-" not in html and "bauteile--" not in html


# --- Liste und Detail ---


def test_list_shows_status_and_hides_retired(client: Client, make_product: MakeProduct) -> None:
    client.force_login(make_inspector(role=Role.VIEWER, qualified=False))
    new = make_product("TST-L1")
    retired = make_product("TST-L2")
    record_event(retired, EventKind.RETIRED, actor=None, comment="Test")

    html = client.get(reverse("inventory:part_list")).content.decode()
    retired_html = client.get(
        reverse("inventory:part_list"), {"status": "retired"}
    ).content.decode()

    assert "TST-L1" in html and "neu, nicht freigegeben" in html
    assert "TST-L2" not in html
    assert "TST-L2" in retired_html and new.name in html


@pytest.mark.parametrize("query", ["tst-l1", "TST - L1", "Beispiel Bergsport", "SN-TEST"])
def test_list_search(query: str, client: Client, make_product: MakeProduct) -> None:
    client.force_login(make_inspector(role=Role.VIEWER, qualified=False))
    make_product("TST-L1")

    html = client.get(reverse("inventory:part_list"), {"q": query}).content.decode()

    assert "TST-L1" in html


def test_list_search_finds_nothing(client: Client, make_product: MakeProduct) -> None:
    client.force_login(make_inspector(role=Role.VIEWER, qualified=False))
    make_product("TST-L1")

    html = client.get(reverse("inventory:part_list"), {"q": "gibt-es-nicht"}).content.decode()

    assert "Keine Teile gefunden" in html


def test_detail_shows_next_due_and_offers_lock(client: Client, make_product: MakeProduct) -> None:
    from datetime import date

    client.force_login(make_inspector(role=Role.LENDER, qualified=False))
    part = make_product("TST-D1")
    adopt_inspection([part], next_due=date(2027, 5, 1))

    html = client.get(reverse("inventory:part_detail", args=[part.pk])).content.decode()

    assert "01.05.2027" in html and "freigegeben" in html
    assert "Sperren" in html and "Entsperren" not in html


def test_viewer_sees_no_lock_form(client: Client, make_product: MakeProduct) -> None:
    client.force_login(make_inspector(role=Role.VIEWER, qualified=False))
    part = make_product("TST-D2")

    html = client.get(reverse("inventory:part_detail", args=[part.pk])).content.decode()

    assert "Grund der Sperre" not in html


# --- Sperren und Entsperren ---


def test_lender_locks_with_comment(client: Client, make_product: MakeProduct) -> None:
    lender = make_inspector(role=Role.LENDER, qualified=False)
    client.force_login(lender)
    part = make_product("TST-K1")

    client.post(reverse("inventory:part_lock", args=[part.pk]), {"comment": "Naht offen"})

    event = part.events.get()
    assert (event.kind, event.comment, event.actor) == (EventKind.LOCKED, "Naht offen", lender)


def test_lock_needs_comment(client: Client, make_product: MakeProduct) -> None:
    client.force_login(make_inspector(role=Role.LENDER, qualified=False))
    part = make_product("TST-K2")

    response = client.post(
        reverse("inventory:part_lock", args=[part.pk]), {"comment": ""}, follow=True
    )

    assert not part.events.exists()
    assert "braucht einen Kommentar" in response.content.decode()


def test_expert_unlocks(client: Client, make_product: MakeProduct) -> None:
    part = make_product("TST-K3")
    record_event(part, EventKind.LOCKED, actor=None, comment="Test")
    client.force_login(make_inspector())

    client.post(reverse("inventory:part_unlock", args=[part.pk]), {"comment": "Sichtprüfung ok"})

    assert not part.is_locked()


@pytest.mark.parametrize("role", [Role.TRAINER, Role.LENDER])
def test_unlock_without_qualification_is_forbidden(
    role: Role, client: Client, make_product: MakeProduct
) -> None:
    """Verbotener Fall: Entsperren nur mit gültiger Sachkunde, auch ein Sachkundiger ohne."""
    part = make_product("TST-K4")
    record_event(part, EventKind.LOCKED, actor=None, comment="Test")
    for user in (make_inspector(role=role, qualified=False), make_inspector(qualified=False)):
        client.force_login(user)

        response = client.post(reverse("inventory:part_unlock", args=[part.pk]), {"comment": "x"})

        assert response.status_code == 403
        assert part.is_locked()


def test_locking_locked_part_shows_message(client: Client, make_product: MakeProduct) -> None:
    part = make_product("TST-K5")
    record_event(part, EventKind.LOCKED, actor=None, comment="Test")
    client.force_login(make_inspector(role=Role.LENDER, qualified=False))

    response = client.post(
        reverse("inventory:part_lock", args=[part.pk]), {"comment": "nochmal"}, follow=True
    )

    assert "bereits gesperrt" in response.content.decode()
    assert part.events.count() == 1
