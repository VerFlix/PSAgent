"""Tägliche Versionsstände der Sicherung auf Unraid ohne Snapshots (#23).

Festlegung (Felix, 21.11.2026): Unraid hat nur XFS-Platten, also keine Snapshots. Statt dessen
legt `deploy/unraid/versionen.sh` auf Unraid jeden Tag eine Kopie der Sicherung an, die der
SFTP-Benutzer des Servers weder sehen noch ändern kann. Aufbewahrt werden 30 Tage täglich und
12 Monate monatlich, wie zuvor bei den Snapshots.
"""

import os
import shutil
import subprocess
import time
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
    assert kept(target) == ["2026-11-22"]
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

    assert result.returncode == 1
    assert "verändert" in result.stderr
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
    daily = [d.isoformat() for d in days(today - timedelta(29), today)]
    monthly = [f"{y}-{m:02}-01" for y, m in [(2025, 12)] + [(2026, m) for m in range(1, 12)]]
    assert kept(target) == sorted(set(daily + monthly))


def test_a_missing_first_of_month_keeps_the_earliest_version_of_that_month(
    source: Path, target: Path
) -> None:
    """War Unraid am Monatsersten aus, zählt der erste Stand danach."""
    for name in ["2026-03-04", "2026-03-05", "2026-04-01"]:
        (target / name).mkdir(parents=True)

    run(source, target, "2026-11-22")

    assert kept(target) == ["2026-03-04", "2026-04-01", "2026-11-22"]


def test_retention_can_be_set(source: Path, target: Path) -> None:
    for name in ["2026-10-01", "2026-11-01", "2026-11-20", "2026-11-21"]:
        (target / name).mkdir(parents=True)

    run(source, target, "2026-11-22", PSAGENT_VERSIONEN_TAGE="2", PSAGENT_VERSIONEN_MONATE="1")

    assert kept(target) == ["2026-11-01", "2026-11-21", "2026-11-22"]


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
    assert kept(target) == ["2026-10-01", "2026-11-25"]


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


# --- Prüfung vor dem Aufräumen ---
#
# Löscht oder ersetzt ein gehackter Server die Sicherung, würde das Aufräumen nach 30 Tagen alle
# täglichen Stände von vor dem Angriff löschen. Darum räumt das Skript nur auf, wenn der neue
# Stand unauffällig ist, und meldet sonst an Unraid und Uptime Kuma.


@pytest.fixture
def alarm(tmp_path: Path) -> Path:
    """Unraid-Meldeskript, das jeden Aufruf in eine Datei schreibt."""
    log = tmp_path / "meldungen"
    notify = tmp_path / "notify"
    notify.write_text(f'#!/usr/bin/env bash\nprintf "%s|" "$@" >> {log}\necho >> {log}\n')
    notify.chmod(0o755)
    return notify


def alarms(notify: Path) -> str:
    log = notify.parent / "meldungen"
    return log.read_text() if log.exists() else ""


def old_versions(target: Path) -> list[str]:
    """Alte Stände; am 22.11.2026 räumt das Aufräumen 2026-03-03 und 2026-10-06 weg."""
    names = ["2026-03-02", "2026-03-03", "2026-10-05", "2026-10-06"]
    for name in names:
        (target / name).mkdir(parents=True)
    return names


def kept(target: Path) -> list[str]:
    return sorted(p.name for p in target.iterdir() if not p.name.startswith("."))


def age(root: Path, hours: float) -> None:
    stamp = time.time() - hours * 3600
    for path in root.rglob("*"):
        os.utime(path, (stamp, stamp))


def test_a_healthy_version_allows_cleaning_up(source: Path, target: Path, alarm: Path) -> None:
    old_versions(target)

    result = run(source, target, "2026-11-22", PSAGENT_UNRAID_NOTIFY=str(alarm))

    assert result.returncode == 0, result.stderr
    assert kept(target) == ["2026-03-02", "2026-10-05", "2026-11-22"]
    assert alarms(alarm) == ""


def test_an_empty_backup_is_reported_and_nothing_is_deleted(
    source: Path, target: Path, alarm: Path
) -> None:
    names = old_versions(target)
    shutil.rmtree(source / "psagent")

    result = run(source, target, "2026-11-22", PSAGENT_UNRAID_NOTIFY=str(alarm))

    assert result.returncode == 1
    assert kept(target) == sorted([*names, "2026-11-22"])
    assert "leer" in result.stderr
    assert "-i|alert|" in alarms(alarm)


def test_a_stale_backup_is_reported_and_nothing_is_deleted(
    source: Path, target: Path, alarm: Path
) -> None:
    """Seit über 26 Stunden kam nichts Neues: Server aus, Sicherung gestört oder gehackt."""
    names = old_versions(target)
    age(source, 27)

    result = run(source, target, "2026-11-22", PSAGENT_UNRAID_NOTIFY=str(alarm))

    assert result.returncode == 1
    assert kept(target) == sorted([*names, "2026-11-22"])
    assert "26 Stunden" in result.stderr
    assert "-i|alert|" in alarms(alarm)


def test_a_shrunk_backup_is_reported_until_someone_accepts_it(
    source: Path, target: Path, alarm: Path
) -> None:
    """Der Vergleich gilt dem letzten unauffälligen Stand, nicht dem Vortag."""
    big = source / "psagent" / "wal_005" / "000000010000000000000002.br"
    big.write_bytes(b"x" * 1000)
    assert run(source, target, "2026-11-20").returncode == 0
    big.unlink()
    names = old_versions(target)

    first = run(source, target, "2026-11-21", PSAGENT_UNRAID_NOTIFY=str(alarm))
    second = run(source, target, "2026-11-22", PSAGENT_UNRAID_NOTIFY=str(alarm))

    assert first.returncode == 1
    assert second.returncode == 1
    assert "geschrumpft" in second.stderr
    assert "2026-11-20" in second.stderr
    assert kept(target) == sorted([*names, "2026-11-20", "2026-11-21", "2026-11-22"])

    accepted = run(source, target, "2026-11-22", PSAGENT_VERSIONEN_STAND_ANNEHMEN="1")
    later = run(source, target, "2026-11-23")

    assert accepted.returncode == 0, accepted.stderr
    assert later.returncode == 0, later.stderr
    assert "2026-10-06" not in kept(target)
    assert "2026-03-03" not in kept(target)


def test_a_moderate_change_in_size_is_fine(source: Path, target: Path) -> None:
    """Alte Vollsicherungen löscht der Server jeden Tag; das ist kein Befund."""
    archives = source / "psagent" / "dateien"
    (archives / "dateien-2026-11-21T020000Z.tar").write_bytes(b"x" * 100)
    run(source, target, "2026-11-21")
    (archives / "dateien-2026-11-21T020000Z.tar").unlink()
    (archives / "dateien-2026-11-22T020000Z.tar").write_bytes(b"x" * 60)

    assert run(source, target, "2026-11-22").returncode == 0


def test_thresholds_can_be_set(source: Path, target: Path) -> None:
    age(source, 2)

    stale = run(source, target, "2026-11-22", PSAGENT_VERSIONEN_FRISCH_STUNDEN="1")

    assert stale.returncode == 1
    assert "älter als 1 Stunde " in stale.stderr

    big = source / "psagent" / "gross"
    big.write_bytes(b"x" * 500)
    assert run(source, target, "2026-11-23").returncode == 0
    big.unlink()
    assert run(source, target, "2026-11-24", PSAGENT_VERSIONEN_MIN_PROZENT="1").returncode == 0


def test_the_reference_is_not_a_version(source: Path, target: Path) -> None:
    run(source, target, "2026-11-21")
    run(source, target, "2026-11-22")

    assert (target / ".referenz").read_text().split() == ["2026-11-22", "10"]
    assert (target / ".referenz").stat().st_mode & 0o777 == 0o600


def test_a_file_changed_in_place_is_a_finding_until_someone_confirms_it(
    source: Path, target: Path, alarm: Path
) -> None:
    """WAL-G, Datei-Archive und Kettenköpfe schreibt der Server nur einmal; Ändern ist ein Befund.

    Größe und Zeitstempel kann ein gehackter Server fälschen. Dass eine Datei von gestern heute
    anders ist, sieht Unraid aber ohne Schlüssel: Sie ist dann kein Hardlink auf gestern.
    """
    run(source, target, "2026-11-20")
    names = old_versions(target)
    wal(source).write_bytes(b"wal X")
    os.utime(wal(source), (time.time() + 60, time.time() + 60))

    changed = run(source, target, "2026-11-21", PSAGENT_UNRAID_NOTIFY=str(alarm))
    still_open = run(source, target, "2026-11-22")

    assert changed.returncode == 1
    assert "1 Datei" in changed.stderr
    assert "wal_005/000000010000000000000001.br" in changed.stderr
    assert "-i|alert|" in alarms(alarm)
    assert still_open.returncode == 1
    assert "Offener Befund vom 2026-11-21" in still_open.stderr
    assert kept(target) == sorted([*names, "2026-11-20", "2026-11-21", "2026-11-22"])

    confirmed = run(source, target, "2026-11-22", PSAGENT_VERSIONEN_STAND_ANNEHMEN="1")
    later = run(source, target, "2026-11-23")

    assert confirmed.returncode == 0, confirmed.stderr
    assert later.returncode == 0, later.stderr
    assert not (target / ".befund").exists()
    assert "2026-10-06" not in kept(target)


def test_a_missing_chain_head_is_a_finding(source: Path, target: Path) -> None:
    """Kettenköpfe löscht der Server nie (sicherung.sh, Schritt 3)."""
    heads = source / "psagent" / "kettenkopf"
    heads.mkdir()
    (heads / "kopf-0001.txt").write_text("1:abc")
    run(source, target, "2026-11-21")
    (heads / "kopf-0001.txt").unlink()
    (heads / "kopf-0002.txt").write_text("2:def")

    result = run(source, target, "2026-11-22")

    assert result.returncode == 1
    assert "Kettenkopf" in result.stderr


def test_a_chain_head_replaced_by_a_folder_is_a_finding(source: Path, target: Path) -> None:
    heads = source / "psagent" / "kettenkopf"
    heads.mkdir()
    (heads / "kopf-0001.txt").write_text("1:abc")
    run(source, target, "2026-11-21")
    (heads / "kopf-0001.txt").unlink()
    (heads / "kopf-0001.txt").mkdir()
    (heads / "kopf-0001.txt" / "x").write_text("x")

    result = run(source, target, "2026-11-22")

    assert result.returncode == 1


def test_a_file_replaced_by_a_folder_is_a_finding(source: Path, target: Path) -> None:
    run(source, target, "2026-11-21")
    wal(source).unlink()
    wal(source).mkdir()
    (wal(source) / "x").write_bytes(b"x" * 10)

    result = run(source, target, "2026-11-22")

    assert result.returncode == 1
    assert "verändert" in result.stderr


def test_file_names_from_the_server_are_cleaned_in_messages(
    source: Path, target: Path, alarm: Path
) -> None:
    """Dateinamen kommen vom Server; in Meldungen nur Buchstaben, Ziffern und ._/-."""
    odd = source / "psagent" / "<script>böse\nzeile&amp;"
    odd.write_bytes(b"1")
    run(source, target, "2026-11-21")
    odd.write_bytes(b"22")

    result = run(source, target, "2026-11-22", PSAGENT_UNRAID_NOTIFY=str(alarm))

    assert result.returncode == 1
    sent = alarms(alarm) + (target / ".befund").read_text()
    assert "<" not in sent
    assert "zeile" in sent
    assert (target / ".befund").read_text().count("\n") == 1


def test_a_timestamp_in_the_future_is_a_finding(source: Path, target: Path) -> None:
    later = time.time() + 2 * 86400
    os.utime(wal(source), (later, later))

    result = run(source, target, "2026-11-22")

    assert result.returncode == 1
    assert "Zukunft" in result.stderr


def test_a_stale_backup_clears_itself_once_new_data_arrives(source: Path, target: Path) -> None:
    """Server aus (Urlaub, Ausfall) ist kein Angriff; dafür braucht es keine Bestätigung."""
    age(source, 27)
    assert run(source, target, "2026-11-21").returncode == 1
    (source / "psagent" / "wal_005" / "000000010000000000000002.br").write_bytes(b"wal 2")

    result = run(source, target, "2026-11-22")

    assert result.returncode == 0, result.stderr
    assert not (target / ".befund").exists()


@pytest.fixture
def kuma(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    """Ein falsches curl im PATH, das die Anfrage an Uptime Kuma in eine Datei schreibt."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "kuma"
    curl = bin_dir / "curl"
    curl.write_text(f'#!/usr/bin/env bash\nprintf "%s|" "$@" >> {log}\necho >> {log}\n')
    curl.chmod(0o755)
    env = {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "PSAGENT_VERSIONEN_PUSH": "http://127.0.0.1:3001/api/push/Beispiel",
    }
    return log, env


def test_reports_success_to_uptime_kuma(
    source: Path, target: Path, kuma: tuple[Path, dict[str, str]]
) -> None:
    log, env = kuma

    run(source, target, "2026-11-22", **env)

    request = log.read_text()
    assert "status=up" in request
    assert "http://127.0.0.1:3001/api/push/Beispiel" in request


def test_reports_findings_and_failures_to_uptime_kuma(
    source: Path, target: Path, tmp_path: Path, kuma: tuple[Path, dict[str, str]]
) -> None:
    log, env = kuma
    age(source, 27)

    run(source, target, "2026-11-22", **env)
    run(tmp_path / "fehlt", target, "2026-11-23", **env)

    requests = log.read_text().splitlines()
    assert len(requests) == 2
    assert all("status=down" in request for request in requests)


def test_without_a_push_address_nothing_is_sent(
    source: Path, target: Path, kuma: tuple[Path, dict[str, str]]
) -> None:
    log, env = kuma
    env.pop("PSAGENT_VERSIONEN_PUSH")

    assert run(source, target, "2026-11-22", **env).returncode == 0
    assert not log.exists()


def test_script_is_valid_bash() -> None:
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)  # noqa: S603, S607
