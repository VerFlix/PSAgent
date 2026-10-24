"""Wiederherstellung aus der Sicherung (#25): Vorbedingungen, Ablauf, Probe und Ernstfall.

Hier mit falschem `docker` (Reihenfolge, Schutz vor Überschreiben, Schlüssel nie auf der
Befehlszeile). Echt mit WAL-G, SFTP und PostgreSQL spielt es der CI-Job „Sicherung und
Wiederherstellung (Probe)“ durch (`deploy/probe/sicherungsprobe.sh`).
"""

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

V2 = Path(__file__).resolve().parents[1]
SCRIPT = V2 / "deploy" / "wiederherstellen.sh"
FAKE = Path(__file__).with_name("deploy_fake_docker.py")
PASSPHRASE = "nur-fuer-den-test"
HEAD = "7:" + "ab" * 32


@pytest.fixture
def host(tmp_path: Path) -> Path:
    """Frisches Gerät nach pi-einrichten.md, Schritt 5, mit falschem `docker`."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    wrapper = bin_dir / "docker"
    wrapper.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE}" "$@"\n')
    wrapper.chmod(0o755)
    etc = tmp_path / "etc"
    (etc / "sicherung").mkdir(parents=True)
    for name in ("image.env", "psagent.env", "sicherung/public.asc"):
        (etc / name).write_text("")
    for name in ("postgres", "media"):
        (tmp_path / "var" / name).mkdir(parents=True)
    (tmp_path / "privat.asc").write_text("")
    return tmp_path


def restore(
    base: Path, *args: str, **env: str
) -> tuple[subprocess.CompletedProcess[str], list[Any]]:
    log = base / "aufrufe.jsonl"
    log.touch()
    result = subprocess.run(  # noqa: S603
        ["bash", str(SCRIPT), *args],  # noqa: S607
        env={
            "PATH": f"{base / 'bin'}:/usr/bin:/bin",
            "FAKE_LOG": str(log),
            "PSAGENT_ETC": str(base / "etc"),
            "PSAGENT_VAR": str(base / "var"),
            "WALG_PGP_KEY_PASSPHRASE": PASSPHRASE,
            **env,
        },
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    return result, calls


def joined(calls: list[Any]) -> list[str]:
    return [" ".join(call["args"]) for call in calls]


# --- Aufruf und Vorbedingungen ---


@pytest.mark.parametrize(
    "args", [[], ["--probe"], ["--test", "privat.asc"], ["--probe", "privat.asc", "--kopf"]]
)
def test_mode_and_key_are_required(host: Path, args: list[str]) -> None:
    result, calls = restore(host, *[str(host / a) if a.endswith(".asc") else a for a in args])

    assert result.returncode == 2
    assert "--probe" in result.stderr and "--ernstfall" in result.stderr
    assert calls == []


def test_head_must_look_like_a_chain_head(host: Path) -> None:
    result, calls = restore(host, "--probe", str(host / "privat.asc"), "--kopf", "7:xyz")

    assert result.returncode == 1
    assert "<Nummer>:<Hash>" in result.stderr
    assert calls == []


@pytest.mark.parametrize("folder", ["postgres", "media"])
def test_nothing_is_overwritten(host: Path, folder: str) -> None:
    existing = host / "var" / folder / "PG_VERSION"
    existing.write_text("17")

    result, calls = restore(host, "--probe", str(host / "privat.asc"))

    assert result.returncode == 1
    assert "nicht leer" in result.stderr
    assert existing.read_text() == "17"
    assert not any(" run " in call for call in joined(calls))


def test_refuses_while_psagent_is_running(host: Path) -> None:
    result, calls = restore(host, "--probe", str(host / "privat.asc"), FAKE_RUNNING="abc123\n")

    assert result.returncode == 1
    assert "läuft noch" in result.stderr
    assert not any(" run " in call for call in joined(calls))


def test_emergency_refuses_on_a_probe_device(host: Path) -> None:
    (host / "etc" / "sicherung" / "probe").write_text("Probe")

    result, calls = restore(host, "--ernstfall", str(host / "privat.asc"))

    assert result.returncode == 1
    assert "Probegerät" in result.stderr
    assert not any(" run " in call for call in joined(calls))


def test_wrong_passphrase_stops_before_fetching(host: Path) -> None:
    result, calls = restore(host, "--probe", str(host / "privat.asc"), FAKE_FAIL="gpg --batch")

    assert result.returncode == 1
    assert "Passphrase" in result.stderr
    assert not any("backup-fetch" in call for call in joined(calls))


# --- Ablauf ---


def test_probe_restores_database_files_and_checks_the_chain(host: Path) -> None:
    result, calls = restore(
        host,
        "--probe",
        str(host / "privat.asc"),
        "--kopf",
        HEAD,
        FAKE_ARCHIVES="dateien-2026-10-23T020000Z.tar.zst dateien-2026-10-24T020000Z.tar.zst",
    )

    assert result.returncode == 0, result.stderr
    assert (host / "etc" / "sicherung" / "probe").exists()
    steps = joined(calls)

    def first(text: str) -> int:
        return next(i for i, call in enumerate(steps) if text in call)

    order = [
        first("gpg --batch"),
        first("wal-g backup-fetch"),
        first("archive_mode=off"),
        first("pg_is_in_recovery()"),
        first("st get"),
        first("up -d db"),
        first("manage.py migrate"),
        first("up -d app"),
        first("kette_pruefen"),
    ]
    assert order == sorted(order)
    assert "restore_command=wal-g wal-fetch %f %p" in steps[first("archive_mode=off")]
    assert '"dateien/$1"' in steps[first("st get")]
    assert steps[first("st get")].endswith("dateien-2026-10-24T020000Z.tar.zst")
    unpack = next(
        call for call in calls if call["args"][-4:] == ["-C", "/var/lib/psagent", "-xf", "-"]
    )
    assert unpack["stdin"] == "ARCHIV"
    assert steps[first("kette_pruefen")].endswith(f"kette_pruefen --kopf {HEAD}")
    assert not any("wal-push" in call or "backup-push" in call for call in steps)
    assert "Wiederhergestellt in etwa" in result.stdout


def test_emergency_continues_the_backup_on_the_new_timeline(host: Path) -> None:
    result, calls = restore(host, "--ernstfall", str(host / "privat.asc"))

    assert result.returncode == 0, result.stderr
    assert not (host / "etc" / "sicherung" / "probe").exists()
    steps = joined(calls)
    history = next(call for call in calls if "wal-push" in " ".join(call["args"]))
    assert history["args"][-1] == "2"
    assert '.history"' in " ".join(history["args"])
    assert any("wal-g backup-push" in call for call in steps)


def test_changed_chain_fails_the_restore(host: Path) -> None:
    result, _ = restore(host, "--probe", str(host / "privat.asc"), FAKE_KETTE="1")

    assert result.returncode == 1
    assert "kette_pruefen: 1" in result.stdout


def test_private_key_and_passphrase_stay_off_the_command_line(host: Path) -> None:
    """Die Passphrase geht nur als Umgebung in kurzlebige Container, nie als Argument."""
    _, calls = restore(host, "--probe", str(host / "privat.asc"))

    for call in calls:
        assert PASSPHRASE not in " ".join(call["args"])
    with_key = [
        call["args"] for call in calls if "/schluessel/privat.asc:ro" in " ".join(call["args"])
    ]
    assert with_key
    for args in with_key:
        assert "-e" in args and "WALG_PGP_KEY_PASSPHRASE" in args
        assert "--rm" in args or "--name" in args
