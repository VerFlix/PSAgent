"""Ende-zu-Ende: eigenen Briefkopf/Stempel unter „Konto“ pflegen, auf allen Geräten (Parität 5)."""

import pytest
from playwright.sync_api import Page, expect

from accounts.models import Role
from tests.e2e.conftest import assert_fits_width, sign_in

pytestmark = [pytest.mark.e2e, pytest.mark.django_db(transaction=True)]


def test_expert_sets_own_stamp_on_devices(device_page: Page, site: str) -> None:
    page = device_page
    user = sign_in(page, site, Role.PPE_EXPERT)

    page.goto(f"{site}/konto/")
    page.get_by_label("Briefkopf/Stempel").fill("Testperson Beispiel\nBeispielverein")
    page.get_by_role("button", name="Speichern").click()

    expect(page.get_by_text("Briefkopf/Stempel gespeichert.")).to_be_visible()
    expect(page.get_by_label("Briefkopf/Stempel")).to_have_value(
        "Testperson Beispiel\nBeispielverein"
    )
    assert_fits_width(page)
    user.refresh_from_db()
    assert user.stamp == "Testperson Beispiel\nBeispielverein"
