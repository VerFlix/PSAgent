"""GAL-Dokumente: einmal gespeichert, mit beliebig vielen Teilen verknüpft (Parität 4)."""

import hashlib
from pathlib import Path

import pytest
from django.db import IntegrityError, transaction

from inventory.models import Document
from inventory.services import link_document, store_document_file
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
