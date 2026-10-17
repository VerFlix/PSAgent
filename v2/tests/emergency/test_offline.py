"""Offline-Kopie der Notfallmappe (Parität 22): Service Worker auf Vereinsgeräten, von Hand sonst.

Was der Service Worker im Browser tut (offline aus dem Speicher liefern), prüft der
Browser-Test `tests/e2e/test_emergency_e2e.py`.
"""

import pytest
from django.test import Client
from django.urls import reverse

from accounts.devices import register_device
from accounts.models import Role, User
from emergency import binder as service
from tests.helpers import make_inspector
from tests.lending.conftest import MakePart

pytestmark = pytest.mark.django_db


def on_club_device(client: Client, user: User) -> None:
    """Macht den Test-Client zum Vereinsgerät (Cookie wie beim Festlegen durch den Admin)."""
    from django.http import HttpResponse

    response = HttpResponse()
    register_device("Tablet Ausgabe", actor=make_inspector(role=Role.ADMIN), response=response)
    client.cookies.update(response.cookies)
    client.force_login(user)


def test_service_worker_script_is_public_and_scoped(client: Client) -> None:
    response = client.get(reverse("emergency:service_worker"))

    assert response.status_code == 200
    assert response["Content-Type"].startswith("application/javascript")
    assert reverse("emergency:service_worker").startswith(reverse("emergency:index"))
    assert b"mappe/neueste.pdf" in response.content


def test_parity_22_latest_supports_etag(client: Client, lender: User, make_part: MakePart) -> None:
    make_part()
    binder = service.create_binder(actor=None)
    client.force_login(lender)

    first = client.get(reverse("emergency:latest"))
    again = client.get(reverse("emergency:latest"), headers={"If-None-Match": first["ETag"]})

    assert first["ETag"] == f'"{binder.sha256}"'
    assert first["Content-Disposition"].startswith("inline")
    assert first["X-Notfallmappe-Stand"]
    assert again.status_code == 304


def test_parity_22_new_binder_changes_etag(
    client: Client, lender: User, make_part: MakePart
) -> None:
    make_part()
    service.create_binder(actor=None)
    client.force_login(lender)
    old = client.get(reverse("emergency:latest"))["ETag"]
    service.create_binder(actor=None)

    response = client.get(reverse("emergency:latest"), headers={"If-None-Match": old})

    assert response.status_code == 200 and response["ETag"] != old


def test_parity_22_club_device_keeps_copy_automatically(client: Client, lender: User) -> None:
    on_club_device(client, lender)

    page = client.get(reverse("home")).content.decode()

    assert 'data-offline-binder="auto"' in page and "js/offline.js" in page


def test_other_devices_only_by_hand(client: Client, lender: User) -> None:
    client.force_login(lender)

    home = client.get(reverse("home")).content.decode()
    emergency = client.get(reverse("emergency:index")).content.decode()

    assert "data-offline-binder" not in home
    assert 'data-offline-binder="manual"' in emergency and "data-offline-enable" in emergency


def test_viewer_on_club_device_loads_no_binder(client: Client) -> None:
    """Verbotener Fall: Viewer sehen keine Personendaten, auch nicht über die Offline-Kopie."""
    on_club_device(client, make_inspector(role=Role.VIEWER, qualified=False))

    page = client.get(reverse("home")).content.decode()

    assert "data-offline-binder" not in page
    assert client.get(reverse("emergency:latest")).status_code == 403
