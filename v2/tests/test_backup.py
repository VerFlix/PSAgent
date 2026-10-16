"""Vollständige Sicherung als ZIP (Parität 11): nur Admin, ohne Zugangsdaten, prüfbar."""

import hashlib
import io
import json
import zipfile
from pathlib import Path

import pytest
from django.test import Client, override_settings
from django.urls import reverse

from accounts.enrollment import create_token
from accounts.models import Passkey, Role
from inventory.services import create_product, store_document_file
from lending.services import create_borrower
from tests.helpers import make_inspector

pytestmark = pytest.mark.django_db
PASSWORD = "ein-langes-Test-Passwort-42"  # gitleaks:allow – künstliches Testpasswort


@pytest.fixture(autouse=True)
def media(tmp_path: Path) -> object:
    with override_settings(MEDIA_ROOT=tmp_path / "media"):
        yield


def download(client: Client) -> zipfile.ZipFile:
    response = client.post(reverse("backup"))
    assert response.status_code == 200
    assert response["Content-Type"] == "application/zip"
    assert "no-store" in response["Cache-Control"]
    return zipfile.ZipFile(io.BytesIO(response.content))


def test_parity_11_backup_contains_data_and_files(client: Client) -> None:
    admin = make_inspector(role=Role.ADMIN)
    client.force_login(admin)
    part = create_product({"designation": "Helm", "ei": "TST-B1"})
    document = store_document_file(b"%PDF-1.4 GAL", "gal.pdf", title="GAL Helm")
    part.documents.add(document)
    create_borrower("Kim Beispiel", email="kim@example.org")

    archive = download(client)

    names = set(archive.namelist())
    assert {"daten.json", "pruefsummen.sha256", f"dateien/{document.sha256}.pdf"} <= names
    records = json.loads(archive.read("daten.json"))
    models = {record["model"] for record in records}
    assert {"inventory.part", "inventory.component", "lending.borrowercontact"} <= models
    assert any(r["fields"].get("name") == "Kim Beispiel" for r in records)


def test_parity_11_checksums_match(client: Client) -> None:
    client.force_login(make_inspector(role=Role.ADMIN))
    store_document_file(b"%PDF-1.4 GAL", "gal.pdf", title="GAL")

    archive = download(client)

    lines = archive.read("pruefsummen.sha256").decode().splitlines()
    assert len(lines) == len(archive.namelist()) - 1
    for line in lines:
        digest, name = line.split("  ", 1)
        assert hashlib.sha256(archive.read(name)).hexdigest() == digest


def test_backup_leaves_out_credentials(client: Client) -> None:
    admin = make_inspector(role=Role.ADMIN)
    admin.set_password(PASSWORD)
    admin.save()
    client.force_login(admin)
    Passkey.objects.create(user=admin, name="YubiKey", credential_id=b"k1", public_key=b"\x01")
    create_token(admin)

    archive = download(client)

    records = json.loads(archive.read("daten.json"))
    models = {record["model"] for record in records}
    assert not models & {"accounts.passkey", "accounts.enrollmenttoken", "accounts.clubdevice"}
    users = [r for r in records if r["model"] == "accounts.user"]
    assert users and all("password" not in r["fields"] for r in users)
    assert b"pbkdf2" not in archive.read("daten.json")


@pytest.mark.parametrize("role", [Role.PPE_EXPERT, Role.TRAINER, Role.LENDER, Role.VIEWER])
def test_only_admin_downloads_backup(role: Role, client: Client) -> None:
    client.force_login(make_inspector(role=role))

    assert client.post(reverse("backup")).status_code == 403


def test_backup_download_is_logged(client: Client, caplog: pytest.LogCaptureFixture) -> None:
    admin = make_inspector(role=Role.ADMIN)
    client.force_login(admin)

    with caplog.at_level("WARNING", logger="psagent.audit"):
        download(client)

    assert f"Vollständige Sicherung heruntergeladen von {admin.username}" in caplog.text


def test_backup_page_explains_content(client: Client) -> None:
    client.force_login(make_inspector(role=Role.ADMIN))

    html = client.get(reverse("backup")).content.decode()

    assert "Enthält Personendaten" in html and "Ohne Zugangsdaten" in html
