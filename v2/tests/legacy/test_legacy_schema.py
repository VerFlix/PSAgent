"""Die Schema-Varianten entsprechen dem, was der echte Altsystem-Code anlegt (Issue #3)."""

import sqlite3
from contextlib import closing
from itertools import combinations
from pathlib import Path

import pytest

from legacy.schema import CURRENT_LEGACY_COMMIT, VARIANTS, TableInfo, describe_schema
from legacy.testdata import build_legacy_db
from tests.legacy.reference import (
    LEGACY_FILES,
    REPO_ROOT,
    export_legacy_code,
    fresh_databases,
    git_file,
    reference_databases,
)


def schema_of(path: Path | None) -> dict[str, TableInfo]:
    if path is None:
        return {}
    with closing(sqlite3.connect(path)) as conn:
        return describe_schema(conn)


@pytest.mark.parametrize("name", list(VARIANTS))
def test_variant_matches_historical_legacy_code(name: str, tmp_path: Path) -> None:
    """Jede Variante: Generator-Schema = Schema aus dem Altsystem-Code des genannten Commits."""
    variant = VARIANTS[name]
    reference_main, reference_archive = reference_databases(variant, tmp_path / "referenz")

    generated = build_legacy_db(tmp_path / "generiert", variant, seed=1)

    assert schema_of(generated.main_db) == schema_of(reference_main)
    assert schema_of(generated.archive_db) == schema_of(reference_archive)


def test_s7_matches_current_legacy_code_in_repo(tmp_path: Path) -> None:
    """Der aktuelle Stand (s7) entspricht dem Altsystem-Code im Arbeitsverzeichnis."""
    code = export_legacy_code(tmp_path / "code")
    main, archive = fresh_databases(code, tmp_path / "db", with_archive=True)

    generated = build_legacy_db(tmp_path / "generiert", "s7", seed=1)

    assert schema_of(generated.main_db) == schema_of(main)
    assert schema_of(generated.archive_db) == schema_of(archive)


def test_legacy_code_in_repo_is_unchanged() -> None:
    """Das Altsystem im Repo ist der Stand CURRENT_LEGACY_COMMIT (CLAUDE.md, Regel 7)."""
    for filename in LEGACY_FILES:
        assert (REPO_ROOT / filename).read_bytes() == git_file(CURRENT_LEGACY_COMMIT, filename)


def test_variants_have_distinct_schemas() -> None:
    """Keine Variante ist eine Dublette einer anderen."""
    schemas = {}
    for name, variant in VARIANTS.items():
        with (
            closing(sqlite3.connect(":memory:")) as main,
            closing(sqlite3.connect(":memory:")) as arc,
        ):
            for statement in variant.main:
                main.execute(statement)
            for statement in variant.archive:
                arc.execute(statement)
            schemas[name] = (describe_schema(main), describe_schema(arc))
    for first, second in combinations(schemas, 2):
        assert schemas[first] != schemas[second], f"{first} und {second} sind gleich"


def test_only_variants_from_s5_on_have_an_archive() -> None:
    for name, variant in VARIANTS.items():
        has_archive_code = variant.base_commit in {"59ec978", "176ac49", "b441ab6"}
        assert variant.has_archive == has_archive_code, name
