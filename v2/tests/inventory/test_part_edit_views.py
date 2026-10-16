"""Teile bearbeiten (Parität 3), GAL an mehrere Teile (Parität 4), GAL im Browser (Parität 21)."""

import re
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from accounts.models import Role, User
from inventory.models import Component, Document, EventKind, Part
from inventory.services import (
    InventoryError,
    attach_document,
    link_document,
    record_event,
    rename_part,
    store_document_file,
)
from tests.helpers import make_inspector
from tests.inventory.conftest import MakeProduct, MakeSystem

pytestmark = pytest.mark.django_db
PDF = b"%PDF-1.4 kuenstliche GAL"


def login(client: Client, role: Role, *, qualified: bool = False) -> User:
    user = make_inspector(role=role, qualified=qualified)
    client.force_login(user)
    return user


def edit_data(part: Part, **changes: Any) -> dict[str, Any]:
    """Das Bearbeiten-Formular so, wie der Browser es mit den gespeicherten Werten schickt."""
    components = list(part.components.order_by("position"))
    data: dict[str, Any] = {
        "name": part.name,
        "no_ppe": "on" if part.has_no_ppe() else "",
        "bauteile-TOTAL_FORMS": str(len(components)),
        "bauteile-INITIAL_FORMS": str(len(components)),
        "bauteile-MIN_NUM_FORMS": "0",
        "bauteile-MAX_NUM_FORMS": "1000",
    }
    for index, component in enumerate(components):
        prefix = f"bauteile-{index}"
        data[f"{prefix}-id"] = str(component.pk)
        for field in (
            "designation",
            "standard",
            "model_name",
            "manufacturer",
            "serial_number",
            "ei",
        ):
            data[f"{prefix}-{field}"] = getattr(component, field)
        data[f"{prefix}-year_of_manufacture"] = component.year_of_manufacture or ""
        for field in ("purchase_date", "first_use_date"):
            value = getattr(component, field)
            data[f"{prefix}-{field}"] = value.isoformat() if value else ""
    if not data["no_ppe"]:
        del data["no_ppe"]
    data.update(changes)
    return data


def edit(client: Client, part: Part, *, confirm: bool, **changes: Any) -> Any:
    data = edit_data(part, **changes)
    if confirm:
        data["confirm"] = "1"
    return client.post(reverse("inventory:part_edit", args=[part.pk]), data)


# --- Parität 3: bearbeiten mit Bestätigung ---


def test_parity_3_edit_shows_confirmation_first(client: Client, make_product: MakeProduct) -> None:
    login(client, Role.TRAINER)
    part = make_product("TST-B1")

    response = edit(client, part, confirm=False, **{"bauteile-0-manufacturer": "Beispiel Alpin"})

    html = response.content.decode()
    assert "Änderungen an" in html and "bestätigen" in html
    assert "Beispiel Bergsport" in html and "Beispiel Alpin" in html
    assert part.components.get().manufacturer == "Beispiel Bergsport"  # noch nichts gespeichert


def test_parity_3_confirmed_edit_is_saved(client: Client, make_product: MakeProduct) -> None:
    login(client, Role.TRAINER)
    part = make_product("TST-B2")

    response = edit(
        client,
        part,
        confirm=True,
        name="Gurt blau",
        **{"bauteile-0-manufacturer": "Beispiel Alpin", "bauteile-0-serial_number": "SN-9"},
    )

    assert response["Location"] == reverse("inventory:part_detail", args=[part.pk])
    part.refresh_from_db()
    component = part.components.get()
    assert (part.name, component.manufacturer, component.serial_number) == (
        "Gurt blau",
        "Beispiel Alpin",
        "SN-9",
    )


def test_parity_3_back_keeps_input(client: Client, make_product: MakeProduct) -> None:
    login(client, Role.TRAINER)
    part = make_product("TST-B3")
    data = edit_data(part, **{"bauteile-0-manufacturer": "Beispiel Alpin"}, back="1")

    html = client.post(reverse("inventory:part_edit", args=[part.pk]), data).content.decode()

    assert "Weiter zur Bestätigung" in html and 'value="Beispiel Alpin"' in html
    assert part.components.get().manufacturer == "Beispiel Bergsport"


def test_parity_3_no_change_saves_nothing(client: Client, make_product: MakeProduct) -> None:
    login(client, Role.TRAINER)
    part = make_product("TST-B4")

    response = edit(client, part, confirm=False)

    assert response.status_code == 302


@pytest.mark.parametrize(
    ("field", "value"),
    [("ei", "TST-NEU"), ("year_of_manufacture", "2020"), ("first_use_date", "2025-01-01")],
)
def test_parity_3_ppe_fields_locked_without_qualification(
    field: str, value: str, client: Client, make_product: MakeProduct
) -> None:
    """Verbotener Fall: Ohne Sachkunde werden PSA-Felder nicht übernommen, auch per Anfrage."""
    login(client, Role.TRAINER)
    part = make_product("TST-B5")
    before = Component.objects.values().get(part=part)

    form = client.get(reverse("inventory:part_edit", args=[part.pk])).content.decode()
    edit(client, part, confirm=True, **{f"bauteile-0-{field}": value})

    tag = re.search(rf'<input[^>]*name="bauteile-0-{field}"[^>]*>', form)
    assert tag is not None and " disabled" in tag.group(0)
    assert Component.objects.values().get(part=part) == before


def test_parity_3_expert_changes_ppe_fields(client: Client, make_product: MakeProduct) -> None:
    login(client, Role.PPE_EXPERT, qualified=True)
    part = make_product("TST-B6")

    edit(
        client,
        part,
        confirm=True,
        **{"bauteile-0-ei": "TST-B6-NEU", "bauteile-0-first_use_date": "2025-01-01"},
    )

    component = part.components.get()
    assert (component.ei, component.first_use_date) == ("TST-B6-NEU", date(2025, 1, 1))


def test_parity_3_no_ppe_toggle_needs_qualification(
    client: Client, make_product: MakeProduct
) -> None:
    part = make_product("TST-B7")
    login(client, Role.TRAINER)
    edit(client, part, confirm=True, no_ppe="on")
    assert not part.has_no_ppe()

    login(client, Role.PPE_EXPERT, qualified=True)
    edit(client, part, confirm=True, no_ppe="on")
    assert part.has_no_ppe()
    edit(client, part, confirm=True, no_ppe="")
    assert not part.has_no_ppe()
    assert [e.kind for e in part.events.order_by("id")] == [
        EventKind.NO_PPE_SET,
        EventKind.NO_PPE_CLEARED,
    ]


def test_parity_3_ei_of_other_part_is_rejected(client: Client, make_product: MakeProduct) -> None:
    login(client, Role.PPE_EXPERT, qualified=True)
    make_product("TST-B8")
    part = make_product("TST-B9")

    response = edit(client, part, confirm=True, **{"bauteile-0-ei": " tst-b8 "})

    assert "ist schon vergeben" in response.content.decode()
    assert part.components.get().ei == "TST-B9"


def test_parity_3_own_ei_with_new_spelling_is_allowed(
    client: Client, make_product: MakeProduct
) -> None:
    login(client, Role.PPE_EXPERT, qualified=True)
    part = make_product("TST-B10")

    edit(client, part, confirm=True, **{"bauteile-0-ei": "tst-b10"})

    assert part.components.get().ei == "tst-b10"


def test_parity_3_system_components_are_edited(client: Client, make_system: MakeSystem) -> None:
    login(client, Role.TRAINER)
    part = make_system("TST-SB1", "TST-SB2")

    edit(client, part, confirm=True, **{"bauteile-1-serial_number": "SN-ZWEI"})

    serials = list(part.components.order_by("position").values_list("serial_number", flat=True))
    assert serials == ["SN-TEST-1", "SN-ZWEI"]


def test_parity_3_component_of_other_part_cannot_be_injected(
    client: Client, make_product: MakeProduct
) -> None:
    """Die Bauteil-ID im Formular kann kein Bauteil eines anderen Teils treffen."""
    login(client, Role.TRAINER)
    other = make_product("TST-B11")
    part = make_product("TST-B12")
    data = edit_data(part, **{"bauteile-0-manufacturer": "Fremd"})
    data["bauteile-0-id"] = str(other.components.get().pk)
    data["confirm"] = "1"

    client.post(reverse("inventory:part_edit", args=[part.pk]), data)

    assert other.components.get().manufacturer == "Beispiel Bergsport"


def test_parity_3_retired_part_cannot_be_edited(client: Client, make_product: MakeProduct) -> None:
    login(client, Role.TRAINER)
    part = make_product("TST-B13")
    record_event(part, EventKind.RETIRED, actor=None, comment="Test")

    response = edit(client, part, confirm=True, name="Neu")

    assert response.status_code == 302
    part.refresh_from_db()
    assert part.name != "Neu"


@pytest.mark.parametrize("role", [Role.LENDER, Role.VIEWER])
def test_rename_needs_manage_parts(role: Role, make_product: MakeProduct) -> None:
    from django.core.exceptions import PermissionDenied

    part = make_product("TST-B14")

    with pytest.raises(PermissionDenied):
        rename_part(part, "Neu", actor=make_inspector(role=role, qualified=False))

    part.refresh_from_db()
    assert part.name != "Neu"


# --- Parität 4: GAL an mehrere Teile ---


def test_parity_4_gal_file_to_several_parts_stored_once(
    client: Client, make_product: MakeProduct
) -> None:
    login(client, Role.TRAINER)
    parts = [make_product(f"TST-G{i}") for i in range(3)]
    ids = [str(part.pk) for part in parts]

    page = client.get(reverse("inventory:gal_assign"), {"teil": ids}).content.decode()
    client.post(
        reverse("inventory:gal_assign"),
        {
            "teil": ids,
            "gal_file": SimpleUploadedFile("gal.pdf", PDF),
            "gal_title": "GAL Testmodell",
        },
    )

    assert "Markiert: 3 Teil(e)" in page
    assert Document.objects.count() == 1
    assert all(part.documents.get().title == "GAL Testmodell" for part in parts)


def test_parity_4_gal_is_required(client: Client, make_product: MakeProduct) -> None:
    login(client, Role.TRAINER)
    part = make_product("TST-G9")

    response = client.post(reverse("inventory:gal_assign"), {"teil": [str(part.pk)]})

    assert "Bitte eine GAL-Datei oder einen Link angeben" in response.content.decode()
    assert not part.documents.exists()


def test_parity_4_no_parts_selected(client: Client) -> None:
    login(client, Role.TRAINER)

    response = client.get(reverse("inventory:gal_assign"), follow=True)

    assert "mindestens ein Teil markieren" in response.content.decode()


def test_parity_4_retired_part_rejects_whole_assignment(make_product: MakeProduct) -> None:
    active = make_product("TST-G10")
    retired = make_product("TST-G11")
    record_event(retired, EventKind.RETIRED, actor=None, comment="Test")
    document = link_document("https://example.org/gal.pdf", title="GAL")

    with pytest.raises(InventoryError, match="Ausgesondert"):
        attach_document([active, retired], document, actor=None)

    assert not active.documents.exists()


def test_parity_4_existing_gal_is_not_added_twice(make_product: MakeProduct) -> None:
    part = make_product("TST-G12")
    document = link_document("https://example.org/gal.pdf", title="GAL")

    assert attach_document([part], document, actor=None) == 1
    assert attach_document([part], document, actor=None) == 0
    assert part.documents.count() == 1


@pytest.mark.parametrize("role", [Role.LENDER, Role.VIEWER])
def test_parity_4_lower_roles_cannot_assign(role: Role, make_product: MakeProduct) -> None:
    from django.core.exceptions import PermissionDenied

    part = make_product("TST-G13")
    document = link_document("https://example.org/gal.pdf", title="GAL")

    with pytest.raises(PermissionDenied):
        attach_document([part], document, actor=make_inspector(role=role, qualified=False))

    assert not part.documents.exists()


def test_list_offers_gal_for_marked_only_from_trainer_up(
    client: Client, make_product: MakeProduct
) -> None:
    """Markieren können alle (Export), GAL zuweisen erst ab Trainer/Materialwart."""
    make_product("TST-G14")
    login(client, Role.LENDER)
    html = client.get(reverse("inventory:part_list")).content.decode()
    assert 'name="teil"' in html and "GAL an markierte Teile" not in html

    login(client, Role.TRAINER)
    assert "GAL an markierte Teile" in client.get(reverse("inventory:part_list")).content.decode()


# --- Parität 21: GAL im Browser ---


def test_parity_21_gal_pdf_opens_in_browser(client: Client, media_root: Path) -> None:
    login(client, Role.VIEWER)
    document = store_document_file(PDF, "gal.pdf", title="GAL Testmodell A1")

    response = client.get(reverse("inventory:document_open", args=[document.pk]))

    assert response.status_code == 200
    assert response["Content-Type"] == "application/pdf"
    assert response["Content-Disposition"].startswith("inline")
    assert "gal-testmodell-a1.pdf" in response["Content-Disposition"]
    assert "sandbox" in response["Content-Security-Policy"]
    assert response["X-Content-Type-Options"] == "nosniff"
    assert response.getvalue() == PDF


def test_parity_21_gal_link_redirects_to_manufacturer(client: Client) -> None:
    login(client, Role.VIEWER)
    document = link_document("https://example.org/gal/a1.pdf", title="GAL A1")

    response = client.get(reverse("inventory:document_open", args=[document.pk]))

    assert response.status_code == 302
    assert response["Location"] == "https://example.org/gal/a1.pdf"


def test_parity_21_detail_links_to_gal(client: Client, make_product: MakeProduct) -> None:
    login(client, Role.VIEWER)
    part = make_product("TST-G20")
    document = store_document_file(PDF, "gal.pdf", title="GAL Datei")
    part.documents.add(document)

    html = client.get(reverse("inventory:part_detail", args=[part.pk])).content.decode()

    assert reverse("inventory:document_open", args=[document.pk]) in html


def test_parity_21_gal_needs_login(client: Client) -> None:
    document = store_document_file(PDF, "gal.pdf", title="GAL")

    response = client.get(reverse("inventory:document_open", args=[document.pk]))

    assert response.status_code == 302
