"""Ende-zu-Ende: Notfallmappe erzeugen und herunterladen, auf allen Geräten (Parität 22)."""

from pathlib import Path
from typing import Any

import pytest
from django.http import HttpResponse
from playwright.sync_api import Page, expect

from accounts.devices import register_device
from accounts.models import Role
from emergency.binder import create_binder, path_of
from inventory.services import create_product
from tests.e2e.conftest import assert_fits_width, sign_in
from tests.helpers import make_inspector

pytestmark = [pytest.mark.e2e, pytest.mark.django_db(transaction=True)]


def test_lender_downloads_binder(
    device_page: Page, site: str, tmp_path: Path, settings: Any
) -> None:
    settings.MEDIA_ROOT = tmp_path / "media"
    page = device_page
    sign_in(page, site, Role.LENDER)
    create_product({"designation": "Klettergurt", "ei": "TST-NB1"})

    page.goto(f"{site}/")
    page.get_by_role("link", name="Notbetrieb und Notfallmappe").click()
    expect(page.get_by_text("Noch keine Notfallmappe erzeugt.")).to_be_visible()
    assert_fits_width(page)

    with page.expect_download() as download:
        page.get_by_role("button", name="Erzeugen und herunterladen").click()
    assert download.value.suggested_filename.startswith("notfallmappe-")
    path = tmp_path / "mappe.pdf"
    download.value.save_as(path)
    assert path.read_bytes().startswith(b"%PDF-")

    page.reload()
    expect(page.get_by_text("A-00001 bis A-00010")).to_be_visible()
    latest = page.request.get(f"{site}/notbetrieb/mappe/neueste.pdf")
    assert latest.body() == path.read_bytes()


CACHED = """async () => {
  const cache = await caches.open("psagent-notfallmappe-v1");
  return Boolean(await cache.match("/notbetrieb/mappe/neueste.pdf"));
}"""
CONTROLLED = "() => navigator.serviceWorker.controller !== null"
FETCH = """async () => {
  try {
    const response = await fetch("/notbetrieb/mappe/neueste.pdf");
    const size = (await response.arrayBuffer()).byteLength;
    return [response.status, response.headers.get("Content-Type"), size];
  } catch (error) {
    return [0, String(error), 0];
  }
}"""


def test_club_device_opens_binder_offline(
    page: Page, site: str, tmp_path: Path, settings: Any
) -> None:
    """Parität 22: Auf dem Vereinsgerät öffnet sich die neueste Mappe auch ohne Verbindung."""
    settings.MEDIA_ROOT = tmp_path / "media"
    response = HttpResponse()
    register_device("Tablet Ausgabe", actor=make_inspector(role=Role.ADMIN), response=response)
    page.context.add_cookies(
        [
            {
                "name": settings.PSAGENT_CLUB_DEVICE_COOKIE,
                "value": response.cookies[settings.PSAGENT_CLUB_DEVICE_COOKIE].value,
                "url": site,
            }
        ]
    )
    sign_in(page, site, Role.LENDER)
    create_product({"designation": "Klettergurt", "ei": "TST-NB2"})
    binder = create_binder(actor=None)

    page.goto(f"{site}/")
    page.wait_for_function(CACHED)
    page.goto(f"{site}/notbetrieb/")
    expect(page.get_by_text("Offline gespeichert:")).to_be_visible()
    # Headless Chrome steuert die erste Seite nach der Registrierung nicht immer schon über den
    # Service Worker; jede weitere Navigation im Bereich /notbetrieb/ schon (so auch das
    # Lesezeichen auf die Mappe ohne Verbindung).
    page.reload()
    page.wait_for_function(CONTROLLED)

    # Server nicht erreichbar: Jede Netzanfrage nach der Mappe scheitert, wie bei einem
    # ausgefallenen Server. Antworten kann dann nur noch der Service Worker aus dem Speicher.
    page.context.route("**/notbetrieb/mappe/neueste.pdf", lambda route: route.abort())
    status, content_type, size = page.evaluate(FETCH)

    assert (status, content_type) == (200, "application/pdf")
    assert size == len(path_of(binder).read_bytes())
