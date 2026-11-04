"""Theke-Einstellungen (#127, Teil 3): Art-Kürzel, Schnellzugriff, nach Größe, Leihdauer.

Festlegung (Felix, 03.11.2026): nur Admin, jede Änderung mit YubiKey, nur angehängt; ohne
Eintrag gelten die Startwerte. „nach Größe“ z. B. später für Helme.
"""

from typing import Any

import pytest
from django.core.exceptions import PermissionDenied
from django.test import Client
from django.urls import reverse

from accounts.models import Role
from core import chain
from counter import config, kinds
from counter.config import Kind, SettingError
from counter.models import CounterSetting
from tests.counter.conftest import MakePart
from tests.helpers import make_inspector
from tests.yubikey_client import login_with_yubikey

pytestmark = pytest.mark.django_db

URL = reverse("counter:settings")
PARTS = reverse("counter:checkout_parts")


def rows(*entries: tuple[str, str, bool, bool], days: int = 7) -> dict[str, Any]:
    """POST-Daten des Formulars: eine Zeile je Art-Kürzel."""
    data: dict[str, Any] = {
        "art-TOTAL_FORMS": str(len(entries)),
        "art-INITIAL_FORMS": "0",
        "art-MIN_NUM_FORMS": "0",
        "art-MAX_NUM_FORMS": "1000",
        "default_days": str(days),
    }
    for index, (code, label, quick, sized) in enumerate(entries):
        data[f"art-{index}-code"] = code
        data[f"art-{index}-label"] = label
        if quick:
            data[f"art-{index}-quick"] = "on"
        if sized:
            data[f"art-{index}-sized"] = "on"
    return data


def test_defaults_without_a_setting() -> None:
    assert [kind.code for kind in kinds.quick_kinds()] == ["G", "KS", "H"]
    assert config.default_days() == 7


def test_newest_setting_counts_and_is_chained() -> None:
    admin = make_inspector(role=Role.ADMIN)
    config.configure([Kind("G", "Klettergurt", True, True)], 3, actor=admin)
    config.configure(
        [
            Kind("g", " Klettergurt ", True, True),
            Kind("H", "Helm", True, True),
            Kind("KL", "Schuh"),
        ],
        14,
        actor=admin,
    )

    assert kinds.all_kinds() == (
        Kind("G", "Klettergurt", True, True),
        Kind("H", "Helm", True, True),
        Kind("KL", "Schuh"),
    )
    assert config.default_days() == 14
    assert CounterSetting.objects.count() == 2
    assert chain.verify() == []


@pytest.mark.parametrize(
    ("entries", "message"),
    [
        ([Kind("G1", "Gurt")], "1 bis 3 Buchstaben"),
        ([Kind("ABCD", "Zu lang")], "1 bis 3 Buchstaben"),
        ([Kind("G", "Gurt"), Kind("g", "Noch ein Gurt")], "doppelt"),
        ([Kind("G", " ")], "fehlt der Name"),
        ([Kind("H", "Helm", quick=False, sized=True)], "nur zusammen mit dem Schnellzugriff"),
        ([Kind(code, code, quick=True) for code in ("A", "B", "C", "D", "E")], "Höchstens 4"),
    ],
)
def test_invalid_settings_are_refused(entries: list[Kind], message: str) -> None:
    with pytest.raises(SettingError, match=message):
        config.configure(entries, 7, actor=make_inspector(role=Role.ADMIN))
    assert not CounterSetting.objects.exists()


def test_duration_must_be_one_of_the_choices() -> None:
    with pytest.raises(SettingError, match="Dauern"):
        config.configure([Kind("G", "Gurt")], 5, actor=make_inspector(role=Role.ADMIN))


@pytest.mark.parametrize("role", [Role.PPE_EXPERT, Role.TRAINER, Role.LENDER, Role.VIEWER])
def test_only_admin_may_configure(role: Role) -> None:
    with pytest.raises(PermissionDenied):
        config.configure([Kind("G", "Gurt")], 7, actor=make_inspector(role=role))


# --- Oberfläche ---


def test_admin_changes_settings_with_yubikey(client: Client) -> None:
    admin = make_inspector(role=Role.ADMIN)
    login_with_yubikey(client, admin)

    page = client.get(URL).content.decode()
    assert 'value="LVS"' in page
    response = client.post(
        URL, rows(("G", "Klettergurt", True, True), ("H", "Helm", True, True), days=3)
    )

    assert response.status_code == 302
    setting = CounterSetting.objects.get()
    assert setting.actor == admin and setting.default_days == 3
    assert [entry["code"] for entry in setting.kinds] == ["G", "H"]


def test_change_without_yubikey_is_refused(client: Client) -> None:
    client.force_login(make_inspector(role=Role.ADMIN))

    response = client.post(URL, rows(("G", "Klettergurt", True, True)))

    assert response.status_code == 403
    assert not CounterSetting.objects.exists()


def test_invalid_form_is_shown_not_stored(client: Client) -> None:
    login_with_yubikey(client, make_inspector(role=Role.ADMIN))

    response = client.post(URL, rows(("G", "Gurt", True, True), ("G", "Doppelt", False, False)))

    assert response.status_code == 400 and "doppelt" in response.content.decode()
    assert not CounterSetting.objects.exists()


def test_deleted_rows_are_left_out(client: Client) -> None:
    login_with_yubikey(client, make_inspector(role=Role.ADMIN))
    data = rows(("G", "Klettergurt", True, True), ("S", "Seil", False, False))
    data["art-1-DELETE"] = "on"

    client.post(URL, data)

    assert [kind.code for kind in kinds.all_kinds()] == ["G"]


def test_lender_sees_no_settings_link(client_lender: Client) -> None:
    page = client_lender.get(reverse("counter:home")).content.decode()

    assert "Theke-Einstellungen" not in page
    assert client_lender.get(URL).status_code == 403


# --- Wirkung an der Theke ---


def test_helmets_by_size_after_the_setting(client_lender: Client, make_part: MakePart) -> None:
    """Festlegung: „nach Größe“ später z. B. für Helme; die Theke bietet dann Größen an."""
    config.configure(
        [Kind("G", "Klettergurt", True, True), Kind("H", "Helm", True, True)],
        7,
        actor=make_inspector(role=Role.ADMIN),
    )
    make_part("DAV-HL-H-1", designation="Helm", size="S-M")
    make_part("DAV-HL-H-2", designation="Helm", size="M-L")
    client_lender.post(reverse("counter:checkout_new"))
    client_lender.post(reverse("counter:checkout_period"), {"tage": 7})

    page = client_lender.get(PARTS).content.decode()
    client_lender.post(PARTS, {"groesse": "H:L"})

    assert 'value="H:L"' in page and "Helm eins mehr" not in page
    line = client_lender.session["psagent_theke_ausgabe"]["lines"][0]
    assert (line["kind"], line["wanted"]) == ("H", "L")


def test_size_for_a_kind_without_sizes_is_refused(
    client_lender: Client, make_part: MakePart
) -> None:
    make_part("DAV-HL-KS-1", designation="Klettersteigset", size="M")
    client_lender.post(reverse("counter:checkout_new"))
    client_lender.post(reverse("counter:checkout_period"), {"tage": 7})

    page = client_lender.post(PARTS, {"groesse": "KS:M"}, follow=True).content.decode()

    assert "Unbekannte Art oder Größe" in page


def test_default_duration_from_the_setting(client_lender: Client) -> None:
    config.configure([Kind("G", "Gurt", True, True)], 14, actor=make_inspector(role=Role.ADMIN))

    page = client_lender.get(reverse("counter:checkout_period")).content.decode()

    assert 'value="14" class="chip on" aria-pressed="true"' in page


def test_new_kind_gets_an_art_key(client_lender: Client) -> None:
    config.configure(
        [Kind("G", "Gurt", True, True), Kind("KL", "Kletterschuh")],
        7,
        actor=make_inspector(role=Role.ADMIN),
    )
    client_lender.post(reverse("counter:checkout_new"))
    client_lender.post(reverse("counter:checkout_period"), {"tage": 7})

    page = client_lender.get(PARTS).content.decode()

    assert 'data-ei-art="KL"' in page and 'data-ei-art="LVS"' not in page
