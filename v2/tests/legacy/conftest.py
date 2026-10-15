"""Hilfen für die Import-Tests (#10). Nur künstliche Datenbanken aus dem Generator (#3)."""

import hashlib
from collections.abc import Callable, Iterator
from datetime import date
from pathlib import Path

import pytest
from django.test import override_settings

from legacy.testdata import DEFAULT_REFERENCE_DATE, GeneratedLegacyData, build_legacy_db

Generate = Callable[..., GeneratedLegacyData]


@pytest.fixture(scope="session")
def generated_sources(tmp_path_factory: pytest.TempPathFactory) -> Generate:
    """Erzeugt künstliche Altdatenbanken einmal je Variante, Startwert und Stichtag.

    Der Import liest nur, deshalb dürfen Tests dieselben Quellen teilen.
    """
    cache: dict[tuple[str, int, bool, date | None], GeneratedLegacyData] = {}

    def generate(
        variant: str = "s7",
        seed: int = 7,
        *,
        edge_cases: bool = True,
        reference_date: date | None = None,
    ) -> GeneratedLegacyData:
        key = (variant, seed, edge_cases, reference_date)
        if key not in cache:
            target = tmp_path_factory.mktemp(f"alt-{variant}-{seed}")
            options = {"reference_date": reference_date} if reference_date else {}
            cache[key] = build_legacy_db(
                target / "quelle", variant, seed, edge_cases=edge_cases, **options
            )
        return cache[key]

    return generate


@pytest.fixture
def fresh_source(tmp_path: Path) -> Generate:
    """Eigene, veränderbare Quelle für Tests, die die Altdaten gezielt anpassen."""

    def generate(
        variant: str = "s7",
        seed: int = 7,
        *,
        edge_cases: bool = True,
        reference_date: date = DEFAULT_REFERENCE_DATE,
    ) -> GeneratedLegacyData:
        return build_legacy_db(
            tmp_path / "quelle",
            variant,
            seed,
            edge_cases=edge_cases,
            reference_date=reference_date,
        )

    return generate


@pytest.fixture
def media_root(tmp_path: Path) -> Iterator[Path]:
    """GAL-Dateien landen im Testordner, nie im Repo."""
    root = tmp_path / "media"
    with override_settings(MEDIA_ROOT=root):
        yield root


def fingerprint(folder: Path) -> dict[str, str]:
    """SHA-256 und Änderungszeit aller Dateien eines Ordners, um Schreibzugriffe zu erkennen."""
    return {
        file.relative_to(folder).as_posix(): (
            f"{hashlib.sha256(file.read_bytes()).hexdigest()}:{file.stat().st_mtime_ns}"
        )
        for file in sorted(folder.rglob("*"))
        if file.is_file()
    }
