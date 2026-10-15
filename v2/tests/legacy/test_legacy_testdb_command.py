"""Tests für den Management-Befehl legacy_testdb (Issue #3)."""

from io import StringIO
from pathlib import Path

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from legacy.schema import VARIANTS


def run(*args: str) -> str:
    out = StringIO()
    call_command("legacy_testdb", *args, stdout=out)
    return out.getvalue()


def test_creates_databases_and_gal_folder(tmp_path: Path) -> None:
    target = tmp_path / "ziel"

    output = run("--variant", "s5", "--seed", "3", "--out", str(target))

    assert (target / "psa.db").is_file()
    assert (target / "psa_archiv.db").is_file()
    assert (target / "GAL").is_dir()
    assert "Variante s5" in output


def test_variant_without_archive_creates_no_archive(tmp_path: Path) -> None:
    run("--variant", "s2", "--out", str(tmp_path))

    assert not (tmp_path / "psa_archiv.db").exists()


def test_second_run_into_same_folder_fails_and_changes_nothing(tmp_path: Path) -> None:
    run("--variant", "s7", "--seed", "1", "--out", str(tmp_path))
    before = (tmp_path / "psa.db").read_bytes()

    with pytest.raises(CommandError, match="überschreibt nichts"):
        run("--variant", "s7", "--seed", "2", "--out", str(tmp_path))

    assert (tmp_path / "psa.db").read_bytes() == before


def test_lists_all_variants() -> None:
    output = run("--liste")

    for name in VARIANTS:
        assert name in output


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (("--variant", "s99"), "Unbekannte Variante"),
        ((), "Zielordner"),
        (("--stichtag", "01.10.2026"), "JJJJ-MM-TT"),
    ],
)
def test_rejects_invalid_arguments(tmp_path: Path, args: tuple[str, ...], message: str) -> None:
    out_args = () if args == () else ("--out", str(tmp_path / "ziel"))

    with pytest.raises(CommandError, match=message):
        run(*args, *out_args)

    assert not (tmp_path / "ziel").exists()
