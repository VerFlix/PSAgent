"""QR-Etiketten (#14): je Bauteil ein Etikett mit QR-Code, EI und Bezeichnung.

- Format: A4-Bogen mit 65 Etiketten 38 x 21,2 mm (5 x 13, z. B. Avery Zweckform 3666/L7651).
- QR-Inhalt: Link `<PSAGENT_BASE_URL>/q/<EI>`, ohne Personendaten. Die normale Kamera-App
  öffnet ihn; nach der Anmeldung führt er zum Teil. Unbekannte und ausgesonderte EI werden
  abgewiesen.
"""

import io
import re
from datetime import timedelta

import pytest
import segno
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone
from pypdf import PdfReader

from accounts.models import Role
from inventory import labels
from inventory.models import EventKind, Part
from inventory.services import create_product, create_system, record_event
from tests.helpers import adopt_inspection, make_inspector

pytestmark = pytest.mark.django_db
BASE = "https://psagent.example.org"


def text_of(pdf: bytes) -> str:
    reader = PdfReader(io.BytesIO(pdf))
    return re.sub(r"\s+", " ", " ".join(page.extract_text() for page in reader.pages))


@pytest.fixture
def trainer_client(client: Client) -> Client:
    client.force_login(make_inspector(role=Role.TRAINER, qualified=False))
    return client


@pytest.fixture
def helmet() -> Part:
    return create_product({"designation": "Kletterhelm", "ei": "TST-Q1"})


# --- Inhalt ---


@override_settings(PSAGENT_BASE_URL=BASE)
def test_qr_links_to_the_ei() -> None:
    assert labels.target_url("TST-Q1") == f"{BASE}/q/TST-Q1"
    assert labels.target_url("AB 12/3") == f"{BASE}/q/AB%2012%2F3"


@override_settings(PSAGENT_BASE_URL=BASE + "/")
def test_trailing_slash_of_base_url_is_ignored() -> None:
    assert labels.target_url("TST-Q1") == f"{BASE}/q/TST-Q1"


def test_one_label_per_component() -> None:
    system = create_system(
        "Klettersteigset", [{"ei": "TST-Q2"}, {"ei": "TST-Q3", "designation": "Y"}]
    )

    entries = labels.entries([system])

    assert [entry["ei"] for entry in entries] == ["TST-Q2", "TST-Q3"]
    assert entries[0]["title"] == "Klettersteigset"


def test_retired_parts_get_no_label(helmet: Part) -> None:
    record_event(helmet, EventKind.RETIRED, actor=None, comment="Schale gerissen")

    assert labels.entries([helmet]) == []


@override_settings(PSAGENT_BASE_URL=BASE)
def test_label_pdf_shows_ei_and_designation(helmet: Part) -> None:
    pdf = labels.render(labels.entries([helmet]), start=1)

    assert pdf.startswith(b"%PDF-")
    text = text_of(pdf)
    assert "TST-Q1" in text and "Kletterhelm" in text


def test_qr_code_is_svg_stable_and_small() -> None:
    """Der QR-Code bleibt bei langer EI klein genug für 16 mm auf dem Etikett (höchstens
    Version 4 = 33 x 33 Module, also etwa 0,5 mm je Modul) und ist bei jedem Druck gleich."""
    url = f"{BASE}/q/" + "TST-" + "X" * 20

    svg = labels.qr_svg(url)

    assert svg.startswith("<svg") and "path" in svg
    assert svg == labels.qr_svg(url)
    assert int(segno.make(url, error="m", micro=False).version) <= 4


@pytest.mark.parametrize(("count", "start", "pages"), [(65, 1, 1), (66, 1, 2), (10, 60, 2)])
def test_sheets_and_start_position(count: int, start: int, pages: int) -> None:
    entries = [
        {"ei": f"TST-{n}", "title": "Gurt", "detail": "", "qr": labels.qr_svg("x")}
        for n in range(count)
    ]

    pdf = labels.render(entries, start=start)

    assert len(PdfReader(io.BytesIO(pdf)).pages) == pages


def test_field_contents_are_set_as_text() -> None:
    part = create_product({"designation": '#panic("boom") ]*_ $x$', "ei": "TST-QT"})

    assert '#panic("boom")' in text_of(labels.render(labels.entries([part]), start=1))


# --- Ansichten und Rechte ---


def test_labels_for_marked_parts(trainer_client: Client, helmet: Part) -> None:
    other = create_product({"designation": "Gurt", "ei": "TST-Q9"})

    response = trainer_client.get(
        reverse("inventory:part_labels"), {"teil": [str(helmet.pk)], "start": "3"}
    )

    assert response["Content-Type"] == "application/pdf"
    assert 'filename="qr-etiketten-' in response["Content-Disposition"]
    text = text_of(response.content)
    assert "TST-Q1" in text and other.components.get().ei not in text


def test_labels_for_all_parts(trainer_client: Client, helmet: Part) -> None:
    create_product({"designation": "Gurt", "ei": "TST-Q9"})

    text = text_of(trainer_client.get(reverse("inventory:part_labels")).content)

    assert "TST-Q1" in text and "TST-Q9" in text


@pytest.mark.parametrize("start", ["0", "66", "abc"])
def test_invalid_start_falls_back_to_first_label(
    start: str, trainer_client: Client, helmet: Part
) -> None:
    response = trainer_client.get(reverse("inventory:part_labels"), {"start": start})

    assert response.status_code == 200 and response.content.startswith(b"%PDF-")


@pytest.mark.parametrize("role", [Role.LENDER, Role.VIEWER])
def test_labels_from_trainer_up(role: Role, client: Client, helmet: Part) -> None:
    """Verbotener Fall: Etiketten gehören zur Pflege der Teile (ab Trainer/Materialwart)."""
    client.force_login(make_inspector(role=role, qualified=False))

    assert client.get(reverse("inventory:part_labels")).status_code == 403


def test_scan_leads_to_the_part(client: Client, helmet: Part) -> None:
    client.force_login(make_inspector(role=Role.VIEWER, qualified=False))

    response = client.get("/q/tst-q1")

    assert response.status_code == 302
    assert response["Location"] == reverse("inventory:part_detail", args=[helmet.pk])


def test_scan_finds_ei_with_spaces_and_slash(client: Client) -> None:
    part = create_product({"designation": "Gurt", "ei": "AB 12/3"})
    client.force_login(make_inspector(role=Role.VIEWER, qualified=False))

    response = client.get("/q/AB%2012%2F3")

    assert response["Location"] == reverse("inventory:part_detail", args=[part.pk])


def test_scan_of_system_component_leads_to_the_system(client: Client) -> None:
    system = create_system("Klettersteigset", [{"ei": "TST-Q2"}, {"ei": "TST-Q3"}])
    client.force_login(make_inspector(role=Role.VIEWER, qualified=False))

    assert client.get("/q/TST-Q3")["Location"] == reverse("inventory:part_detail", args=[system.pk])


def test_unknown_ei_is_refused(client: Client) -> None:
    client.force_login(make_inspector(role=Role.VIEWER, qualified=False))

    response = client.get("/q/TST-GIBTSNICHT")

    assert response.status_code == 404
    assert "Kein Teil mit der EI" in response.content.decode()


def test_retired_part_is_refused(client: Client, helmet: Part) -> None:
    record_event(helmet, EventKind.RETIRED, actor=None, comment="Schale gerissen")
    client.force_login(make_inspector(role=Role.VIEWER, qualified=False))

    response = client.get("/q/TST-Q1")

    assert response.status_code == 410
    assert "ausgesondert" in response.content.decode()


def test_scan_needs_login(client: Client, helmet: Part) -> None:
    response = client.get("/q/TST-Q1")

    assert response.status_code == 302 and "next=/q/TST-Q1" in response["Location"]


def test_detail_offers_labels_and_booking(client: Client, helmet: Part) -> None:
    client.force_login(make_inspector(role=Role.TRAINER, qualified=False))

    page = client.get(reverse("inventory:part_detail", args=[helmet.pk])).content.decode()

    assert f"{reverse('inventory:part_labels')}?teil={helmet.pk}" in page
    assert f"{reverse('lending:new_booking')}?teil={helmet.pk}" in page


def test_new_booking_preselects_part_from_link(client: Client, helmet: Part) -> None:
    adopt_inspection([helmet], next_due=timezone.localdate() + timedelta(days=365))
    client.force_login(make_inspector(role=Role.LENDER, qualified=False))

    page = client.get(reverse("lending:new_booking"), {"teil": helmet.pk}).content.decode()

    assert re.search(rf'value="{helmet.pk}"[^>]*checked', page)
