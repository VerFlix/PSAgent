"""Export der Teileliste als CSV oder PDF (Parität 11)."""

import csv
import io
from datetime import date
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse

from accounts.models import Role
from inventory import export
from inventory.models import EventKind
from inventory.services import create_product, create_system, link_document, record_event
from tests.helpers import adopt_inspection, make_inspector

pytestmark = pytest.mark.django_db
Stock = dict[str, Any]


@pytest.fixture
def viewer_client(client: Client) -> Client:
    client.force_login(make_inspector(role=Role.VIEWER, qualified=False))
    return client


def read_csv(content: bytes) -> list[list[str]]:
    assert content.startswith(b"\xef\xbb\xbf")  # BOM für Excel
    return list(csv.reader(io.StringIO(content.decode("utf-8-sig")), delimiter=";"))


@pytest.fixture
def stock() -> Stock:
    helmet = create_product(
        {"designation": "Helm", "manufacturer": "Beispiel Bergsport", "ei": "TST-X1"}
    )
    helmet.documents.add(link_document("https://example.org/gal.pdf", title="GAL Helm"))
    adopt_inspection([helmet], next_due=date(2027, 5, 1))
    system = create_system("Klettersteigset", [{"ei": "TST-X2"}, {"ei": "TST-X3"}])
    retired = create_product({"designation": "Seil alt", "ei": "TST-X4"})
    record_event(retired, EventKind.RETIRED, actor=None, comment="Alter")
    return {"helmet": helmet, "system": system, "retired": retired}


def test_parity_11_csv_one_row_per_component(viewer_client: Client, stock: Stock) -> None:
    response = viewer_client.get(reverse("inventory:part_export"), {"format": "csv"})

    assert response["Content-Type"].startswith("text/csv")
    assert 'filename="teileliste-' in response["Content-Disposition"]
    table = read_csv(response.content)
    assert table[0][:4] == ["Teil-Nr.", "Teil", "Art", "Bauteil"]
    eis = [row[table[0].index("EI")] for row in table[1:]]
    assert eis == ["TST-X1", "TST-X2", "TST-X3"]  # ohne ausgesonderte
    helmet = table[1]
    assert helmet[table[0].index("nächste Prüfung")] == "01.05.2027"
    assert helmet[table[0].index("Status")] == "freigegeben"
    assert helmet[table[0].index("GAL")] == "GAL Helm"


def test_parity_11_with_retired(viewer_client: Client, stock: Stock) -> None:
    response = viewer_client.get(
        reverse("inventory:part_export"), {"format": "csv", "ausgesondert": "1"}
    )

    eis = [row[11] for row in read_csv(response.content)[1:]]
    assert "TST-X4" in eis


def test_parity_11_marked_parts_only(viewer_client: Client, stock: Stock) -> None:
    response = viewer_client.get(
        reverse("inventory:part_export"),
        {"format": "csv", "teil": [str(stock["system"].pk), str(stock["retired"].pk)]},
    )

    eis = [row[11] for row in read_csv(response.content)[1:]]
    assert eis == ["TST-X2", "TST-X3", "TST-X4"]  # markierte auch ausgesondert


@pytest.mark.parametrize("value", ['=HYPERLINK("x")', "+1", "-2", "@SUM(A1)", "\tx"])
def test_parity_11_csv_neutralises_formulas(value: str) -> None:
    """CSV-Injection: Formeln aus Feldinhalten führt die Tabellenkalkulation nicht aus."""
    create_product({"designation": value, "ei": "TST-F1"})

    table = read_csv(export.to_csv(export.rows(export.parts_for_export(None, with_retired=False))))

    assert table[1][table[0].index("Produktbezeichnung")] == f"'{value}"


def test_parity_11_pdf(viewer_client: Client, stock: Stock) -> None:
    response = viewer_client.get(reverse("inventory:part_export"), {"format": "pdf"})

    assert response["Content-Type"] == "application/pdf"
    assert response.content.startswith(b"%PDF-")
    assert "attachment" in response["Content-Disposition"]


def test_parity_11_pdf_treats_content_as_text() -> None:
    """Feldinhalte mit Typst-Syntax werden gesetzt, nicht ausgeführt."""
    create_product({"designation": '#panic("boom") #read("/etc/passwd") ]*_ $x$', "ei": "TST-T1"})

    pdf = export.to_pdf(
        export.rows(export.parts_for_export(None, with_retired=False)), title="Test", stand="x"
    )

    assert pdf.startswith(b"%PDF-")


def test_parity_11_unknown_format_falls_back_to_csv(viewer_client: Client, stock: Stock) -> None:
    response = viewer_client.get(reverse("inventory:part_export"), {"format": "exe"})

    assert response["Content-Type"].startswith("text/csv")


def test_export_contains_no_personal_data(viewer_client: Client, stock: Stock) -> None:
    from lending.services import create_borrower, reserve

    reserve(
        create_borrower("Kim Beispiel", email="kim@example.org"),
        [stock["helmet"]],
        date(2026, 12, 1),
        date(2026, 12, 2),
        actor=None,
    )

    content = viewer_client.get(reverse("inventory:part_export"), {"format": "csv"}).content

    assert b"Kim" not in content and b"example.org/gal" not in content


def test_export_needs_login(client: Client) -> None:
    assert client.get(reverse("inventory:part_export")).status_code == 302


def test_list_offers_export_and_marking_to_viewers(viewer_client: Client, stock: Stock) -> None:
    html = viewer_client.get(reverse("inventory:part_list")).content.decode()

    assert 'name="teil"' in html and "Markierte als CSV" in html
    assert "GAL an markierte Teile" not in html
