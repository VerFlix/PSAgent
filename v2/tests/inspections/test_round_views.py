"""Prüfmodus im Browser (Parität 7): Seite, Scan, Ergebnisse, Abschluss, Rechte."""

import pytest
from django.test import Client
from django.urls import reverse

from accounts.models import Role, User
from inspections import services
from inspections.models import Outcome, RoundState
from inspections.round_views import scanned_ei
from tests.helpers import make_inspector
from tests.inspections.conftest import MakePart, in_days

pytestmark = pytest.mark.django_db
HTMX = {"HX-Request": "true"}


def ei(part: object) -> str:
    return str(part.components.get().ei)  # type: ignore[attr-defined]


@pytest.fixture
def expert_client(client: Client, inspector: User) -> Client:
    client.force_login(inspector)
    return client


@pytest.mark.parametrize(
    ("scanned", "expected"),
    [
        ("TST-P0001", "TST-P0001"),
        ("  tst-p0001 ", "tst-p0001"),
        ("https://psagent.example.org/q/TST-P0001", "TST-P0001"),
        ("https://psagent.example.org/q/AB%2012", "AB 12"),
    ],
)
def test_scanned_value_gives_the_ei(scanned: str, expected: str) -> None:
    assert scanned_ei(scanned) == expected


def test_parity_7_start_and_continue(expert_client: Client, inspector: User) -> None:
    page = expert_client.get(reverse("inspections:round_mode")).content.decode()
    assert "Prüfungsrunde beginnen" in page

    expert_client.post(reverse("inspections:round_start"))
    page = expert_client.get(reverse("inspections:round_mode")).content.decode()

    round_ = services.open_round(inspector)
    assert round_ is not None and str(round_) in page
    assert 'id="scan-form"' in page and "jsQR-1.4.0.js" in page


def test_parity_7_scan_adds_part_without_page_change(
    expert_client: Client, inspector: User, make_part: MakePart
) -> None:
    round_ = services.start_round(actor=inspector)
    part = make_part()

    response = expert_client.post(
        reverse("inspections:round_scan", args=[round_.pk]),
        {"ei": f"https://psagent.example.org/q/{ei(part)}"},
        headers=HTMX,
    )

    text = response.content.decode()
    assert response.status_code == 200 and text.startswith('<section id="runde"')
    assert f"{part} hinzugefügt." in text and "Noch ohne Ergebnis (1)" in text
    assert services.pending_parts(round_) == [part]


def test_unknown_ei_is_reported(expert_client: Client, inspector: User) -> None:
    round_ = services.start_round(actor=inspector)

    response = expert_client.post(
        reverse("inspections:round_scan", args=[round_.pk]), {"ei": "TST-NIX"}, headers=HTMX
    )

    assert "EI „TST-NIX“ ist unbekannt." in response.content.decode()


def test_parity_7_results_and_close(
    expert_client: Client, inspector: User, make_part: MakePart
) -> None:
    round_ = services.start_round(actor=inspector)
    defect, *rest = make_part(), make_part(), make_part()
    for part in (defect, *rest):
        services.collect(round_, part, actor=inspector)

    expert_client.post(
        reverse("inspections:round_result", args=[round_.pk, defect.pk]),
        {"outcome": Outcome.DEFECT, "comment": "Naht offen", "next_due": ""},
        headers=HTMX,
    )
    response = expert_client.post(
        reverse("inspections:round_pass_remaining", args=[round_.pk]),
        {"next_due": in_days(365).isoformat()},
        headers=HTMX,
    )
    assert "2 Teil(e) in Ordnung." in response.content.decode()

    response = expert_client.post(reverse("inspections:round_close", args=[round_.pk]), follow=True)

    assert round_.state() == RoundState.CLOSED
    page = response.content.decode()
    assert "sobald sie signiert ist" in page and "Abgeschlossen, noch nicht signiert" in page


def test_retire_ignores_next_due(
    expert_client: Client, inspector: User, make_part: MakePart
) -> None:
    round_ = services.start_round(actor=inspector)
    part = make_part()

    expert_client.post(
        reverse("inspections:round_result", args=[round_.pk, part.pk]),
        {"outcome": Outcome.RETIRE, "comment": "Kantenschaden", "next_due": in_days(365)},
    )

    [result] = round_.current_results()
    assert result.outcome == Outcome.RETIRE and result.next_due is None


def test_missing_comment_is_reported(
    expert_client: Client, inspector: User, make_part: MakePart
) -> None:
    round_ = services.start_round(actor=inspector)
    part = make_part()

    response = expert_client.post(
        reverse("inspections:round_result", args=[round_.pk, part.pk]),
        {"outcome": Outcome.DEFECT, "comment": " "},
        headers=HTMX,
    )

    assert "braucht einen Kommentar" in response.content.decode()
    assert not round_.results.exists()


def test_cancel_needs_a_reason(expert_client: Client, inspector: User) -> None:
    round_ = services.start_round(actor=inspector)

    expert_client.post(reverse("inspections:round_cancel", args=[round_.pk]), {"comment": ""})
    assert round_.state() == RoundState.OPEN

    expert_client.post(reverse("inspections:round_cancel", args=[round_.pk]), {"comment": "Test"})
    assert round_.state() == RoundState.CANCELLED


def test_part_page_adds_to_round(
    expert_client: Client, inspector: User, make_part: MakePart
) -> None:
    part = make_part()
    page = expert_client.get(reverse("inventory:part_detail", args=[part.pk])).content.decode()

    expert_client.post(reverse("inspections:part_to_round", args=[part.pk]))

    assert "Zur Prüfungsrunde hinzufügen" in page
    round_ = services.open_round(inspector)
    assert round_ is not None and services.pending_parts(round_) == [part]


def test_home_links_round_mode(expert_client: Client) -> None:
    assert "Prüfmodus" in expert_client.get(reverse("home")).content.decode()


# --- Rechte ---


def test_foreign_round_is_not_found(client: Client, inspector: User, make_part: MakePart) -> None:
    """Verbotener Fall: Eine andere sachkundige Person erreicht die Runde nicht."""
    round_ = services.start_round(actor=inspector)
    client.force_login(make_inspector())

    response = client.post(
        reverse("inspections:round_scan", args=[round_.pk]), {"ei": ei(make_part())}
    )

    assert response.status_code == 404
    assert services.pending_parts(round_) == []


@pytest.mark.parametrize("role", [Role.TRAINER, Role.LENDER, Role.VIEWER])
def test_below_ppe_expert_sees_no_round_mode(
    role: Role, client: Client, make_part: MakePart
) -> None:
    client.force_login(make_inspector(role=role))
    part = make_part()

    page = client.get(reverse("inventory:part_detail", args=[part.pk])).content.decode()

    assert client.get(reverse("inspections:round_mode")).status_code == 403
    assert client.post(reverse("inspections:part_to_round", args=[part.pk])).status_code == 403
    assert "Zur Prüfungsrunde hinzufügen" not in page


def test_expert_without_qualification_is_refused(client: Client) -> None:
    client.force_login(make_inspector(qualified=False))

    assert client.get(reverse("inspections:round_mode")).status_code == 403
