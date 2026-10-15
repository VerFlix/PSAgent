"""Management-Befehl `legacy_import` (#10)."""

from io import StringIO
from pathlib import Path

import pytest
from django.core.management import CommandError, call_command

from inventory.models import Part
from tests.legacy.conftest import Generate

pytestmark = pytest.mark.django_db


def run(*args: str) -> str:
    out = StringIO()
    call_command("legacy_import", *args, stdout=out)
    return out.getvalue()


def test_dry_run_prints_report_and_writes_nothing(
    generated_sources: Generate, media_root: Path
) -> None:
    output = run(str(generated_sources().main_db.parent), "--probelauf")

    assert output.startswith("# Importbericht (Probelauf)")
    assert "## Anzahl je Tabelle" in output
    assert not Part.objects.exists()


def test_import_writes_report_file(
    generated_sources: Generate, media_root: Path, tmp_path: Path
) -> None:
    report = tmp_path / "bericht.md"

    run(str(generated_sources().main_db.parent), "--bericht", str(report))

    assert report.read_text(encoding="utf-8").startswith("# Importbericht (Import)")
    assert Part.objects.exists()


def test_existing_report_file_is_not_overwritten(
    generated_sources: Generate, tmp_path: Path
) -> None:
    report = tmp_path / "bericht.md"
    report.write_text("alt", encoding="utf-8")

    with pytest.raises(CommandError, match="überschreibt nichts"):
        run(str(generated_sources().main_db.parent), "--probelauf", "--bericht", str(report))

    assert report.read_text(encoding="utf-8") == "alt"


def test_errors_become_command_errors(tmp_path: Path) -> None:
    with pytest.raises(CommandError, match="fehlt"):
        run(str(tmp_path), "--probelauf")
