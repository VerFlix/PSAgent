"""Dateien für den Betrieb auf dem Pi (#22): Compose, Umgebungsvorlage, Timer, Update-Skript.

Festlegungen (Felix, 24.10.2026): Image aus der CI von GHCR, HTTPS mit `tailscale serve` (ohne
Caddy), tägliche Befehle als systemd-Timer. Erprobt wird auf dem Pi (lokal); hier wird geprüft,
was sich ohne Pi prüfen lässt.
"""

import configparser
import os
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml
from django.core.management import get_commands

V2 = Path(__file__).resolve().parents[1]
DEPLOY = V2 / "deploy"
SETTINGS = V2 / "psagent" / "settings"
# Befehle, die im Betrieb regelmäßig laufen müssen (Runbooks).
SCHEDULED = {"kettenkopf", "kette_pruefen", "erinnerungen", "loeschfristen", "notfallmappe"}


def compose() -> dict[str, Any]:
    data = yaml.safe_load((DEPLOY / "compose.yaml").read_text())
    assert isinstance(data, dict)
    return data


def settings_variables(*names: str) -> tuple[set[str], set[str]]:
    """Umgebungsvariablen der Einstellungen: (alle, Pflicht ohne Standardwert)."""
    source = "\n".join((SETTINGS / name).read_text() for name in names)
    every = set(re.findall(r'env(?:_list)?\("([A-Z0-9_]+)"', source))
    required = set(re.findall(r'(?<!_)env\("([A-Z0-9_]+)"\)', source))
    return every, required


# --- Compose ---


def test_app_listens_on_localhost_only() -> None:
    """Nur `tailscale serve` auf dem Pi erreicht die App; kein Port ins LAN oder Internet."""
    services = compose()["services"]

    assert services["app"]["ports"] == ["127.0.0.1:8000:8000"]
    assert "ports" not in services["db"]


def test_app_comes_from_ghcr_with_a_pinned_version() -> None:
    image = compose()["services"]["app"]["image"]

    assert image.startswith("ghcr.io/verflix/psagent:${PSAGENT_IMAGE_TAG:?")


def test_secrets_come_from_a_file_outside_the_repo() -> None:
    services = compose()["services"]

    for name in ("app", "db"):
        assert services[name]["env_file"] == ["/etc/psagent/psagent.env"], name
        assert services[name]["restart"] == "unless-stopped", name
        environment = services[name].get("environment", {})
        assert not any("PASSWORD" in key or "SECRET" in key for key in environment), name


def test_trusted_proxy_is_the_gateway_of_the_network() -> None:
    """Anfragen von `tailscale serve` kommen über das Gateway des Docker-Netzes in den Container."""
    data = compose()
    gateway = data["networks"]["psagent"]["ipam"]["config"][0]["gateway"]

    assert data["services"]["app"]["environment"]["PSAGENT_TRUSTED_PROXIES"] == gateway


# --- Umgebungsvorlage ---


def example_variables() -> dict[str, str]:
    lines = (DEPLOY / "psagent.env.example").read_text().splitlines()
    pairs = (line.split("=", 1) for line in lines if line and not line.startswith("#"))
    return {name: value for name, value in pairs}


def test_example_covers_every_required_variable() -> None:
    _, required = settings_variables("base.py", "prod.py")
    provided = set(example_variables()) | set(compose()["services"]["app"]["environment"])

    assert required <= provided, sorted(required - provided)


def test_example_names_only_known_variables() -> None:
    every, _ = settings_variables("base.py", "prod.py")
    database = {"POSTGRES_PASSWORD"}  # gilt auch für den PostgreSQL-Container

    assert set(example_variables()) <= every | database, sorted(set(example_variables()) - every)


def test_example_holds_no_secrets() -> None:
    values = example_variables()

    for name in ("POSTGRES_PASSWORD", "DJANGO_SECRET_KEY"):
        assert values[name].startswith("<") and values[name].endswith(">"), name


# --- Timer ---


def unit(path: Path) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str  # type: ignore[assignment,method-assign]
    parser.read(path)
    return parser


def test_every_scheduled_command_has_a_timer() -> None:
    commands = get_commands()
    timed = set()
    for path in (DEPLOY / "systemd").glob("*.timer"):
        timer = unit(path)
        target = timer["Timer"]["Unit"]
        match = re.fullmatch(r"psagent-befehl@([a-z_]+)\.service", target)
        assert match, f"{path.name}: {target}"
        assert match.group(1) in commands, f"{path.name}: unbekannter Befehl {match.group(1)}"
        assert timer["Timer"]["OnCalendar"], path.name
        assert timer["Timer"]["Persistent"] == "true", path.name
        timed.add(match.group(1))

    assert timed == SCHEDULED


def test_command_unit_uses_the_manage_script_from_the_clone() -> None:
    service = unit(DEPLOY / "systemd" / "psagent-befehl@.service")["Service"]

    assert service["Type"] == "oneshot"
    assert service["ExecStart"] == "/opt/psagent/v2/deploy/manage.sh %I"


# --- Skripte ---


@pytest.mark.parametrize("script", ["update.sh", "manage.sh"])
def test_scripts_are_valid_bash_and_executable(script: str) -> None:
    """Die Timer rufen `manage.sh` direkt auf; ohne Ausführrecht scheitern sie still im Journal."""
    subprocess.run(["bash", "-n", str(DEPLOY / script)], check=True)  # noqa: S603, S607
    assert os.access(DEPLOY / script, os.X_OK)


def test_manage_script_passes_arguments_into_the_container(tmp_path: Path) -> None:
    """Ohne Terminal (Timer) mit `-T`; Argumente kommen unverändert an, auch mit Leerzeichen."""
    fake = tmp_path / "docker"
    fake.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$@"\n')
    fake.chmod(0o755)
    command = ["bash", str(DEPLOY / "manage.sh"), "loeschfristen", "--probelauf", "a b"]

    result = subprocess.run(  # noqa: S603
        command,
        env={"PATH": f"{tmp_path}:/usr/bin:/bin"},
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=True,
    )

    assert result.stdout.splitlines() == [
        "compose",
        "--project-directory",
        str(DEPLOY),
        "--env-file",
        "/etc/psagent/image.env",
        "exec",
        "-T",
        "app",
        "python",
        "manage.py",
        "loeschfristen",
        "--probelauf",
        "a b",
    ]


@pytest.mark.parametrize("tag", ["latest", "sha-123", "v2; rm -rf /", ""])
def test_update_script_rejects_unknown_versions(tag: str) -> None:
    """Vor jedem Docker-Aufruf: nur sha-<Commit> oder v2."""
    command = ["bash", str(DEPLOY / "update.sh"), tag]  # fester Befehl, Tag nur als Argument
    result = subprocess.run(command, capture_output=True, text=True)  # noqa: S603

    assert result.returncode != 0
    assert "docker" not in result.stderr.lower() or "Version" in result.stderr
