"""Sicherung nach Unraid (#23): DB-Image mit WAL-G, Compose, nächtliches Skript.

Festlegungen (Felix, 24.10.2026): eigenes DB-Image mit dem offiziellen WAL-G-Build, Dateien als
nächtliches tar-Archiv mit demselben Schlüssel, der Server löscht nach 30 Vollsicherungen; die
Monatsstände halten die Snapshots auf Unraid. Den echten Durchlauf mit WAL-G, SFTP und
Wiederherstellung macht der CI-Job „Sicherung und Wiederherstellung (Probe)“.
"""

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

V2 = Path(__file__).resolve().parents[1]
DEPLOY = V2 / "deploy"
FAKE = Path(__file__).with_name("deploy_fake_docker.py")


def compose() -> dict[str, Any]:
    data = yaml.safe_load((DEPLOY / "compose.yaml").read_text())
    assert isinstance(data, dict)
    return data


# --- DB-Image ---


def test_db_image_pins_wal_g_for_both_architectures() -> None:
    dockerfile = (DEPLOY / "db" / "Dockerfile").read_text()
    downloads = re.findall(
        r"ADD --checksum=sha256:([0-9a-f]{64}) \\\s+"
        r"https://github\.com/wal-g/wal-g/releases/download/(v[0-9.]+)/wal-g-pg-[0-9.]+-(\w+)\.tar\.gz",
        dockerfile,
    )

    assert {arch for _, _, arch in downloads} == {"amd64", "aarch64"}
    assert len({version for _, version, _ in downloads}) == 1
    assert "FROM postgres:17\n" in dockerfile


def test_db_comes_from_ghcr_with_the_same_version_as_the_app() -> None:
    services = compose()["services"]
    tag = services["app"]["image"].split(":", 1)[1]

    assert services["db"]["image"] == f"ghcr.io/verflix/psagent-db:{tag}"


# --- Compose: WAL-Archiv ---


def postgres_settings() -> dict[str, str]:
    command = compose()["services"]["db"]["command"]
    assert command[0] == "postgres"
    pairs = command[1:]
    assert pairs[::2] == ["-c"] * (len(pairs) // 2)
    return dict(setting.split("=", 1) for setting in pairs[1::2])


def test_every_wal_segment_goes_out_within_a_minute() -> None:
    settings = postgres_settings()

    assert settings["archive_mode"] == "on"
    assert settings["archive_timeout"] == "60"
    assert settings["archive_command"].endswith("wal-g wal-push %p")


def test_a_restore_probe_never_writes_into_the_real_backup() -> None:
    """Im Probebetrieb verwirft PostgreSQL die WAL-Abschnitte (`wiederherstellen.sh --probe`)."""
    command = postgres_settings()["archive_command"]

    assert command.startswith("test -e /etc/psagent/sicherung/probe || ")


def test_the_server_can_write_backups_but_not_read_them() -> None:
    """Nur der öffentliche Schlüssel liegt auf dem Server; kein privater, keine Passphrase."""
    db = compose()["services"]["db"]
    environment = db["environment"]
    example = (DEPLOY / "psagent.env.example").read_text()

    assert [name for name in environment if "PGP" in name] == ["WALG_PGP_KEY_PATH"]
    assert environment["WALG_PGP_KEY_PATH"] == "/etc/psagent/sicherung/public.asc"
    assert "/etc/psagent/sicherung:/etc/psagent/sicherung:ro" in db["volumes"]
    assert "PGP" not in example


def test_backup_runs_nightly_from_the_clone() -> None:
    timer = (DEPLOY / "systemd" / "psagent-sicherung.timer").read_text()
    service = (DEPLOY / "systemd" / "psagent-sicherung.service").read_text()

    assert "OnCalendar=*-*-* 02:00:00\n" in timer
    assert "Persistent=true\n" in timer
    assert "Unit=psagent-sicherung.service\n" in timer
    assert "ExecStart=/opt/psagent/v2/deploy/sicherung.sh\n" in service
    assert (DEPLOY / "sicherung.sh").stat().st_mode & 0o111


# --- Skript ---


@pytest.fixture
def docker(tmp_path: Path) -> Path:
    """Verzeichnis mit falschem `docker`; Aufrufe landen in `tmp_path/aufrufe.jsonl`."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    wrapper = bin_dir / "docker"
    wrapper.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE}" "$@"\n')
    wrapper.chmod(0o755)
    (tmp_path / "etc" / "sicherung").mkdir(parents=True)
    return tmp_path


def run_backup(base: Path, **env: str) -> tuple[subprocess.CompletedProcess[str], list[Any]]:
    log = base / "aufrufe.jsonl"
    log.touch()
    result = subprocess.run(  # noqa: S603
        ["bash", str(DEPLOY / "sicherung.sh")],  # noqa: S607
        env={
            "PATH": f"{base / 'bin'}:/usr/bin:/bin",
            "FAKE_LOG": str(log),
            "PSAGENT_ETC": str(base / "etc"),
            **env,
        },
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    return result, calls


def command(call: dict[str, Any]) -> list[str]:
    """Der Befehl im Container, ohne `docker compose … exec -T [-u postgres] <dienst>`."""
    args: list[str] = call["args"]
    rest = args[args.index("exec") + 2 :]
    if rest[:3] == ["-u", "postgres", "db"]:
        return rest[3:]
    return rest[1:]


def test_backup_runs_database_files_heads_and_cleanup(docker: Path) -> None:
    archives = " ".join(f"dateien-2026-09-{day:02d}T020000Z.tar.zst" for day in range(1, 33))
    result, calls = run_backup(
        docker,
        FAKE_HEADS=" ".join(f"kettenkopf-2026-10-{day}-{day - 17}.txt" for day in (22, 23, 24)),
        FAKE_STORED_HEADS="kettenkopf-2026-10-22-5.txt",
        FAKE_ARCHIVES=archives,
    )

    assert result.returncode == 0, result.stderr
    commands = [command(call) for call in calls]
    assert commands[0] == ["wal-g", "backup-push"]
    assert commands[1][:4] == ["tar", "--numeric-owner", "-C", "/var/lib/psagent"]
    put = calls[2]
    assert command(put)[:4] == ["wal-g", "st", "put", "-s"]
    assert re.fullmatch(r"dateien/dateien-\d{4}-\d\d-\d\dT\d{6}Z\.tar", command(put)[4])
    assert put["stdin"] == "TAR-INHALT"
    uploads = {
        command(call)[-1]: call["stdin"]
        for call in calls
        if command(call)[:3] == ["wal-g", "st", "put"] and "--no-encrypt" in call["args"]
    }
    assert uploads == {
        "kettenkopf/kettenkopf-2026-10-23-6.txt": (
            "INHALT /var/lib/psagent/kettenkopf/kettenkopf-2026-10-23-6.txt"
        ),
        "kettenkopf/kettenkopf-2026-10-24-7.txt": (
            "INHALT /var/lib/psagent/kettenkopf/kettenkopf-2026-10-24-7.txt"
        ),
    }
    assert ["wal-g", "delete", "retain", "FULL", "30", "--confirm"] in commands
    removed = [command(call)[-1] for call in calls if command(call)[:3] == ["wal-g", "st", "rm"]]
    assert removed == [
        "dateien/dateien-2026-09-01T020000Z.tar.zst",
        "dateien/dateien-2026-09-02T020000Z.tar.zst",
    ]


def test_heads_are_stored_readable_and_files_encrypted(docker: Path) -> None:
    """Lesbar auf Unraid sind nur die Kettenköpfe (Konzept); alles andere ist verschlüsselt."""
    _, calls = run_backup(docker, FAKE_HEADS="kettenkopf-2026-10-24-7.txt")

    for call in calls:
        if command(call)[:3] == ["wal-g", "st", "put"]:
            readable = "--no-encrypt" in call["args"]
            assert readable == command(call)[-1].startswith("kettenkopf/"), call


def test_failed_database_backup_keeps_old_backups(docker: Path) -> None:
    result, calls = run_backup(docker, FAKE_FAIL="backup-push")

    assert result.returncode == 1
    commands = [command(call) for call in calls]
    assert not any(cmd[:2] == ["wal-g", "delete"] for cmd in commands)
    assert any(cmd[:4] == ["wal-g", "st", "put", "-s"] for cmd in commands), "Dateien fehlen"
    assert "Vollsicherung" in result.stderr


def test_failed_file_archive_keeps_old_archives(docker: Path) -> None:
    archives = " ".join(f"dateien-2026-09-{day:02d}T020000Z.tar.zst" for day in range(1, 33))
    result, calls = run_backup(docker, FAKE_FAIL="tar", FAKE_ARCHIVES=archives)

    assert result.returncode == 1
    assert not any(command(call)[:3] == ["wal-g", "st", "rm"] for call in calls)


def test_unexpected_head_names_are_reported_not_uploaded(docker: Path) -> None:
    result, calls = run_backup(docker, FAKE_HEADS="../psagent.env kettenkopf-2026-10-24-7.txt")

    assert result.returncode == 1
    targets = [command(call)[-1] for call in calls if command(call)[:3] == ["wal-g", "st", "put"]]
    assert "kettenkopf/kettenkopf-2026-10-24-7.txt" in targets
    assert not any("psagent.env" in target for target in targets)
    assert "unerwartetem Namen" in result.stderr


def test_backup_does_nothing_in_probe_mode(docker: Path) -> None:
    (docker / "etc" / "sicherung" / "probe").touch()

    result, calls = run_backup(docker)

    assert result.returncode == 0
    assert calls == []
    assert "Probebetrieb" in result.stderr
