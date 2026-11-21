"""Tägliche Versionsstände der Sicherung auf Unraid ohne Snapshots (#23).

Festlegung (Felix, 21.11.2026): Unraid hat nur XFS-Platten, also keine Snapshots. Statt dessen
legt `deploy/unraid/versionen.sh` auf Unraid jeden Tag eine Kopie der Sicherung an, die der
SFTP-Benutzer des Servers weder sehen noch ändern kann. Aufbewahrt werden 30 Tage täglich und
12 Monate monatlich, wie zuvor bei den Snapshots.
"""

import os
import shutil
import subprocess
from datetime import date, timedelta
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "deploy" / "unraid" / "versionen.sh"

pytestmark = pytest.mark.skipif(shutil.which("rsync") is None, reason="rsync fehlt")


def run(source: Path, target: Path, today: str, **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        ["bash", str(SCRIPT), str(source), str(target)],  # noqa: S607
        env={**os.environ, "PSAGENT_HEUTE": today, **env},
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture
def source(tmp_path: Path) -> Path:
    """Aufbau wie im Share, in den der Server per SFTP schreibt."""
    root = tmp_path / "psagent-sicherung"
    (root / "psagent" / "wal_005").mkdir(parents=True)
    (root / "psagent" / "wal_005" / "000000010000000000000001.br").write_bytes(b"wal 1")
    (root / "psagent" / "dateien").mkdir()
    (root / "psagent" / "dateien" / "dateien-2026-11-21T020000Z.tar").write_bytes(b"tar 1")
    return root


@pytest.fixture
def target(tmp_path: Path) -> Path:
    return tmp_path / "psagent-versionen"


def wal(version: Path) -> Path:
    return version / "psagent" / "wal_005" / "000000010000000000000001.br"


def test_first_run_copies_the_backup_into_a_dated_folder(source: Path, target: Path) -> None:
    result = run(source, target, "2026-11-22")

    assert result.returncode == 0, result.stderr
    assert sorted(p.name for p in target.iterdir()) == ["2026-11-22"]
    assert wal(target / "2026-11-22").read_bytes() == b"wal 1"


def test_only_root_can_open_the_versions(source: Path, target: Path) -> None:
    """Der SFTP-Benutzer darf die Stände weder sehen noch ändern."""
    run(source, target, "2026-11-22")

    assert target.stat().st_mode & 0o777 == 0o700
    assert (target / "2026-11-22").stat().st_mode & 0o777 == 0o700
    assert wal(target / "2026-11-22").stat().st_mode & 0o777 == 0o600


def test_unchanged_files_share_their_space_with_the_day_before(source: Path, target: Path) -> None:
    run(source, target, "2026-11-22")
    run(source, target, "2026-11-23")

    assert wal(target / "2026-11-22").stat().st_ino == wal(target / "2026-11-23").stat().st_ino


def test_versions_never_share_a_file_with_the_backup(source: Path, target: Path) -> None:
    """Sonst könnte der Server über die Sicherung auch die Stände ändern."""
    run(source, target, "2026-11-22")

    assert wal(target / "2026-11-22").stat().st_ino != wal(source).stat().st_ino


def test_overwritten_or_deleted_files_stay_in_older_versions(source: Path, target: Path) -> None:
    """Ein gehackter Server überschreibt eine Datei an Ort und Stelle und löscht eine andere."""
    run(source, target, "2026-11-22")
    with wal(source).open("r+b") as handle:
        handle.write(b"kaputt")
    (source / "psagent" / "dateien" / "dateien-2026-11-21T020000Z.tar").unlink()

    result = run(source, target, "2026-11-23")

    assert result.returncode == 0, result.stderr
    assert wal(target / "2026-11-22").read_bytes() == b"wal 1"
    tar = Path("psagent") / "dateien" / "dateien-2026-11-21T020000Z.tar"
    assert (target / "2026-11-22" / tar).read_bytes() == b"tar 1"
    assert not (target / "2026-11-23" / tar).exists()


def test_symbolic_links_in_the_backup_are_not_followed(
    source: Path, target: Path, tmp_path: Path
) -> None:
    secret = tmp_path / "geheim"
    secret.write_text("nur root")
    (source / "psagent" / "link").symlink_to(secret)

    result = run(source, target, "2026-11-22")

    assert result.returncode == 0, result.stderr
    assert not (target / "2026-11-22" / "psagent" / "link").exists()
    assert not (target / "2026-11-22" / "psagent" / "link").is_symlink()


def test_second_run_on_the_same_day_keeps_the_first_version(source: Path, target: Path) -> None:
    run(source, target, "2026-11-22")
    wal(source).write_bytes(b"wal 2")

    result = run(source, target, "2026-11-22")

    assert result.returncode == 0, result.stderr
    assert wal(target / "2026-11-22").read_bytes() == b"wal 1"


def test_an_unfinished_copy_is_replaced(source: Path, target: Path) -> None:
    """Bricht ein Lauf ab (Stromausfall), bleibt kein halber Stand mit Datum liegen."""
    (target / ".unfertig" / "rest").mkdir(parents=True)

    result = run(source, target, "2026-11-22")

    assert result.returncode == 0, result.stderr
    assert not (target / ".unfertig").exists()
    assert sorted(p.name for p in (target / "2026-11-22").iterdir()) == ["psagent"]


def days(first: date, last: date) -> list[date]:
    return [first + timedelta(n) for n in range((last - first).days + 1)]


def test_keeps_30_days_and_the_first_version_of_each_of_12_months(
    source: Path, target: Path
) -> None:
    today = date(2026, 11, 22)
    for day in days(date(2025, 6, 15), today - timedelta(1)):
        (target / day.isoformat()).mkdir(parents=True)

    result = run(source, target, today.isoformat())

    assert result.returncode == 0, result.stderr
    kept = sorted(p.name for p in target.iterdir())
    daily = [d.isoformat() for d in days(today - timedelta(29), today)]
    monthly = [f"{y}-{m:02}-01" for y, m in [(2025, 12)] + [(2026, m) for m in range(1, 12)]]
    assert kept == sorted(set(daily + monthly))


def test_a_missing_first_of_month_keeps_the_earliest_version_of_that_month(
    source: Path, target: Path
) -> None:
    """War Unraid am Monatsersten aus, zählt der erste Stand danach."""
    for name in ["2026-03-04", "2026-03-05", "2026-04-01"]:
        (target / name).mkdir(parents=True)

    run(source, target, "2026-11-22")

    assert sorted(p.name for p in target.iterdir()) == ["2026-03-04", "2026-04-01", "2026-11-22"]


def test_retention_can_be_set(source: Path, target: Path) -> None:
    for name in ["2026-10-01", "2026-11-01", "2026-11-20", "2026-11-21"]:
        (target / name).mkdir(parents=True)

    run(source, target, "2026-11-22", PSAGENT_VERSIONEN_TAGE="2", PSAGENT_VERSIONEN_MONATE="1")

    assert sorted(p.name for p in target.iterdir()) == ["2026-11-01", "2026-11-21", "2026-11-22"]


def test_other_folders_in_the_target_are_left_alone(source: Path, target: Path) -> None:
    (target / "notizen").mkdir(parents=True)

    run(source, target, "2026-11-22")

    assert (target / "notizen").is_dir()


def test_a_file_named_like_a_date_is_ignored(source: Path, target: Path) -> None:
    target.mkdir()
    (target / "2026-11-21").write_text("keine Version")

    result = run(source, target, "2026-11-22")

    assert result.returncode == 0, result.stderr
    assert (target / "2026-11-21").read_text() == "keine Version"
    assert (target / "2026-11-22").is_dir()


@pytest.mark.parametrize("share", ["/mnt/user/psagent-versionen", "/mnt/user0/psagent-versionen"])
def test_refuses_a_target_on_the_user_share(source: Path, share: str) -> None:
    """Unter /mnt/user kann ein Share über mehrere Platten gehen; Hardlinks gehen nur auf einer."""
    result = run(source, Path(share), "2026-11-22")

    assert result.returncode == 1
    assert "/mnt/diskN" in result.stderr


def test_refuses_a_target_inside_the_backup(source: Path) -> None:
    """Dort könnte der SFTP-Benutzer die Stände ändern."""
    result = run(source, source / "versionen", "2026-11-22")

    assert result.returncode == 1
    assert not (source / "versionen").exists()


def test_stops_when_the_backup_folder_is_missing(tmp_path: Path, target: Path) -> None:
    result = run(tmp_path / "fehlt", target, "2026-11-22")

    assert result.returncode == 1
    assert not target.exists()


def test_stops_when_the_disk_of_the_target_is_not_mounted(source: Path, tmp_path: Path) -> None:
    """Bei gestopptem Array fehlt /mnt/diskN; die Kopie landete sonst im Arbeitsspeicher."""
    result = run(source, tmp_path / "disk1" / "psagent-versionen", "2026-11-22")

    assert result.returncode == 1
    assert "Array" in result.stderr
    assert not (tmp_path / "disk1").exists()


def test_stops_when_the_clock_is_behind_the_newest_version(source: Path, target: Path) -> None:
    """Mit falscher Uhr würde das Aufräumen die falschen Stände löschen."""
    (target / "2026-11-25").mkdir(parents=True)
    (target / "2026-10-01").mkdir()

    result = run(source, target, "2026-11-22")

    assert result.returncode == 1
    assert sorted(p.name for p in target.iterdir()) == ["2026-10-01", "2026-11-25"]


def test_reports_failures_to_unraid(source: Path, target: Path, tmp_path: Path) -> None:
    """Unraid zeigt die Meldung in der Oberfläche und schickt sie weiter, wenn eingerichtet."""
    log = tmp_path / "meldung"
    notify = tmp_path / "notify"
    notify.write_text(f'#!/usr/bin/env bash\nprintf "%s\\n" "$@" > {log}\n')
    notify.chmod(0o755)

    result = run(tmp_path / "fehlt", target, "2026-11-22", PSAGENT_UNRAID_NOTIFY=str(notify))

    assert result.returncode == 1
    assert "-i\nalert" in log.read_text()
    assert "fehlt" in log.read_text()


def test_script_is_valid_bash() -> None:
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)  # noqa: S603, S607
