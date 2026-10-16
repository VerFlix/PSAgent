"""PDF-Dokumente je Teil (Parität 5): Einsatzdokumentation, PD und Haftungsausschluss.

Wie im Altsystem leere Formulare zum Ausfüllen auf Papier, mit Typst gesetzt. Neu:
- Name und Stempel des Sachkundigen kommen aus dem Konto statt fest aus der Vorlage.
- PD und Haftungsausschluss erzeugen nur Personen mit gültiger Sachkunde (Felix, 16.10.2026).
- Alle Bauteile stehen in einer Tabelle, nicht nur drei.
"""

import io
import re
from datetime import date

import pytest
from django.test import Client, override_settings
from django.urls import reverse
from pypdf import PdfReader

from accounts.models import Role, User
from inventory import documents
from inventory.models import EventKind, Part
from inventory.services import create_product, create_system, record_event
from tests.helpers import make_inspector

pytestmark = pytest.mark.django_db
STAMP = "Testperson Stempel\nBeispielverein Bergsport"
URLS = {
    "einsatzdokumentation": "inventory:part_usage_log",
    "pd": "inventory:part_data_sheet",
    "haftungsausschluss": "inventory:part_disclaimer",
}


def text_of(pdf: bytes) -> str:
    """Text aller Seiten, Leerraum zusammengefasst (Typst bricht Zeilen in Zellen um)."""
    reader = PdfReader(io.BytesIO(pdf))
    return re.sub(r"\s+", " ", " ".join(page.extract_text() for page in reader.pages))


@pytest.fixture
def product() -> Part:
    return create_product(
        {
            "designation": "Klettergurt",
            "standard": "EN 12277",
            "model_name": "Testgurt Alpin",
            "manufacturer": "Beispiel Bergsport",
            "year_of_manufacture": 2024,
            "purchase_date": date(2024, 3, 1),
            "first_use_date": date(2024, 4, 2),
            "ei": "TST-D1",
            "serial_number": "SN-4711",
        }
    )


@pytest.fixture
def system() -> Part:
    return create_system(
        "Klettersteigset Test",
        [{"ei": f"TST-S{number}", "designation": f"Bauteil {number}"} for number in range(1, 6)],
    )


@pytest.fixture
def expert() -> User:
    return make_inspector(stamp_text=STAMP)


def download(client: Client, kind: str, part: Part) -> bytes:
    response = client.get(reverse(URLS[kind], args=[part.pk]))
    assert response.status_code == 200, response.status_code
    assert response["Content-Type"] == "application/pdf"
    assert "attachment" in response["Content-Disposition"]
    content = b"".join(response.streaming_content)  # type: ignore[attr-defined]
    assert content.startswith(b"%PDF-")
    return content


# --- Inhalt ---


def test_parity_5_data_sheet_with_stamp_from_account(
    client: Client, expert: User, product: Part
) -> None:
    client.force_login(expert)

    text = text_of(download(client, "pd", product))

    assert "PSA-Produktdatenblatt (PD)" in text and "Überprüfungsnachweis" in text
    assert "Testperson Stempel" in text and "Beispielverein Bergsport" in text
    for value in ("Klettergurt", "EN 12277", "Testgurt Alpin", "Beispiel Bergsport", "2024"):
        assert value in text
    assert "01.03.2024" in text and "02.04.2024" in text
    assert "TST-D1" in text and "SN-4711" in text


def test_parity_5_disclaimer_names_the_expert(client: Client, expert: User, product: Part) -> None:
    client.force_login(expert)

    text = text_of(download(client, "haftungsausschluss", product))

    assert "Haftungsausschluss für ausgesonderte Produkte" in text
    assert f"Die sachkundige Person: {expert.get_full_name()}" in text
    assert "Testperson Stempel" in text and "TST-D1" in text
    assert "Unterschrift Empfänger*in" in text


def test_parity_5_stamp_defaults_to_full_name(client: Client, product: Part) -> None:
    expert = make_inspector()
    client.force_login(expert)

    text = text_of(download(client, "pd", product))

    assert expert.get_full_name() in text


@override_settings(PSAGENT_CLUB_NAME="Beispielverein Sektion Test")
def test_parity_5_usage_log_with_club_as_owner(client: Client, product: Part) -> None:
    client.force_login(make_inspector(role=Role.TRAINER, qualified=False))

    text = text_of(download(client, "einsatzdokumentation", product))

    assert "Einsatzdokumentation" in text and "Briefkopf/Stempel Besitzer*in" in text
    assert "Beispielverein Sektion Test" in text
    assert "Datum Ausgabe" in text and "Unterschrift des Zurückgebende*r" in text
    assert "TST-D1" in text


def test_parity_5_all_components_of_a_system(client: Client, expert: User, system: Part) -> None:
    """Das Altsystem setzte nur drei Bauteile; hier stehen alle in der Tabelle."""
    client.force_login(expert)

    for kind in URLS:
        text = text_of(download(client, kind, system))
        assert "Klettersteigset Test" in text
        assert all(f"TST-S{number}" in text for number in range(1, 6)), kind


def test_parity_5_document_for_retired_part(client: Client, expert: User, product: Part) -> None:
    """Der Haftungsausschluss gehört gerade zu ausgesonderten Teilen (Archiv im Altsystem)."""
    record_event(product, EventKind.RETIRED, actor=None, comment="Naht beschädigt")
    client.force_login(expert)

    assert "TST-D1" in text_of(download(client, "haftungsausschluss", product))


def test_filename_like_legacy(client: Client, expert: User, system: Part, product: Part) -> None:
    """Altsystem: System nach Namen, Produkt nach EI."""
    client.force_login(expert)

    by_name = client.get(reverse(URLS["pd"], args=[system.pk]))
    by_ei = client.get(reverse(URLS["haftungsausschluss"], args=[product.pk]))

    assert 'filename="Klettersteigset_Test_PD.pdf"' in by_name["Content-Disposition"]
    assert 'filename="TST-D1_Haftungsausschluss.pdf"' in by_ei["Content-Disposition"]


def test_field_contents_are_set_as_text() -> None:
    """Feldinhalte mit Typst-Syntax werden gesetzt, nicht ausgeführt."""
    part = create_product(
        {"designation": '#panic("boom") #read("/etc/passwd") ]*_ $x$', "ei": "X1"}
    )
    expert = make_inspector(stamp_text='#panic("stempel")')

    for kind in documents.DocumentKind:
        pdf = documents.render(kind, part, user=expert)
        assert '#panic("boom")' in text_of(pdf)


# --- Rechte ---


@pytest.mark.parametrize("kind", ["pd", "haftungsausschluss"])
@pytest.mark.parametrize(
    ("role", "qualified"),
    [
        (Role.ADMIN, False),
        (Role.PPE_EXPERT, False),
        (Role.TRAINER, True),
        (Role.LENDER, True),
        (Role.VIEWER, True),
    ],
)
def test_data_sheet_and_disclaimer_need_valid_qualification(
    kind: str, role: Role, qualified: bool, client: Client, product: Part
) -> None:
    """Verbotener Fall: ohne gültige Sachkunde (oder unter PSA-Sachkundig) kein PD und
    kein Haftungsausschluss, denn beide tragen Name und Stempel des Sachkundigen."""
    client.force_login(make_inspector(role=role, qualified=qualified))

    assert client.get(reverse(URLS[kind], args=[product.pk])).status_code == 403


@pytest.mark.parametrize("role", [Role.LENDER, Role.VIEWER])
def test_usage_log_from_trainer_up(role: Role, client: Client, product: Part) -> None:
    """Verbotener Fall: Einsatzdokumentation wie „PDFs erzeugen“ erst ab Trainer."""
    client.force_login(make_inspector(role=role))

    assert client.get(reverse(URLS["einsatzdokumentation"], args=[product.pk])).status_code == 403


def test_detail_offers_documents_by_right(client: Client, product: Part) -> None:
    url = reverse("inventory:part_detail", args=[product.pk])
    pd = reverse(URLS["pd"], args=[product.pk])
    usage = reverse(URLS["einsatzdokumentation"], args=[product.pk])

    client.force_login(make_inspector(role=Role.TRAINER, qualified=False))
    trainer = client.get(url).content.decode()
    client.force_login(make_inspector())
    expert = client.get(url).content.decode()
    client.force_login(make_inspector(role=Role.LENDER, qualified=False))
    lender = client.get(url).content.decode()

    assert usage in trainer and pd not in trainer
    assert usage in expert and pd in expert
    assert usage not in lender and pd not in lender
