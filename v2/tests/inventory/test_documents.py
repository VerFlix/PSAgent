"""GAL-Dokumente: einmal gespeichert, mit beliebig vielen Teilen verknüpft (Parität 4)."""

import hashlib
from pathlib import Path

import pytest
from django.db import IntegrityError, transaction

from inventory.models import Document
from inventory.services import InventoryError, link_document, store_document_file
from tests.inventory.conftest import MakeProduct

PDF = b"%PDF-1.4\n% Platzhalter-GAL, kuenstliche Testdaten\n%%EOF\n"


def test_same_file_is_stored_once(make_product: MakeProduct, media_root: Path) -> None:
    first = store_document_file(PDF, "gal klettergurt.pdf", title="GAL Klettergurt")
    second = store_document_file(PDF, "andere-kopie.pdf", title="Kopie")

    assert first == second
    assert first.sha256 == hashlib.sha256(PDF).hexdigest()
    assert len([p for p in media_root.rglob("*") if p.is_file()]) == 1


def test_document_can_belong_to_many_parts(make_product: MakeProduct) -> None:
    document = store_document_file(PDF, "gal.pdf", title="GAL Gurt")
    parts = [make_product(), make_product(), make_product()]
    for part in parts:
        part.documents.add(document)

    assert set(document.parts.all()) == set(parts)


def test_stored_file_content_is_unchanged(media_root: Path, db: None) -> None:
    document = store_document_file(PDF, "gal.pdf", title="GAL")

    with document.file.open("rb") as handle:
        assert handle.read() == PDF


def test_link_document_is_reused(db: None) -> None:
    first = link_document("https://example.org/gal/testmodell.pdf", title="GAL online")
    second = link_document("https://example.org/gal/testmodell.pdf", title="anderer Titel")

    assert first == second
    assert first.url == "https://example.org/gal/testmodell.pdf"


def test_document_is_either_file_or_link(db: None) -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        Document.objects.create(title="leer")
    with pytest.raises(IntegrityError), transaction.atomic():
        Document.objects.create(
            title="beides", file="gal/x.pdf", sha256="a" * 64, url="https://example.org/x.pdf"
        )


def test_file_hash_is_unique(db: None) -> None:
    Document.objects.create(title="eins", file="gal/a.pdf", sha256="b" * 64)

    with pytest.raises(IntegrityError), transaction.atomic():
        Document.objects.create(title="zwei", file="gal/b.pdf", sha256="b" * 64)


@pytest.mark.parametrize(
    "content",
    [b"<html><script>alert(1)</script></html>", b"MZ\x90\x00", b"", b" %PDF-1.4"],
)
def test_only_pdf_files_are_accepted(content: bytes, media_root: Path, db: None) -> None:
    with pytest.raises(InventoryError, match="kein PDF"):
        store_document_file(content, "gal.pdf", title="keine PDF")

    assert Document.objects.count() == 0
    assert not media_root.exists() or not any(media_root.rglob("*.*"))


def test_stored_file_always_ends_in_pdf(db: None) -> None:
    document = store_document_file(PDF, "gal.html", title="falsche Endung")

    assert str(document.file.name).endswith(".pdf")


@pytest.mark.parametrize(
    "url",
    ["javascript:alert(1)", "data:text/html,<script>", "ftp://example.org/gal.pdf", "gal.pdf"],
)
def test_only_http_links_are_accepted(url: str, db: None) -> None:
    with pytest.raises(InventoryError, match="Ungültiger Link"):
        link_document(url, title="GAL")
    with pytest.raises(IntegrityError), transaction.atomic():
        Document.objects.create(title="GAL", url=url)


def test_database_rejects_non_pdf_file_names(db: None) -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        Document.objects.create(title="GAL", file="gal/x.html", sha256="c" * 64)
