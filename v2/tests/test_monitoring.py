"""Monitoring (#24): Herzschlag je Timer an Uptime Kuma und stündlicher Zustand.

Festlegungen (Felix, 24.10.2026): Uptime Kuma auf Unraid, Herzschlag je Prüfung, Meldung per
ntfy aufs Handy, Zustand stündlich. Erreichbarkeit und HTTPS-Zertifikat prüft Uptime Kuma selbst
(`/healthz`). Erprobt wird auf Pi und Unraid (lokal); hier wird geprüft, was ohne beide geht.
"""

import configparser
import subprocess
import sys
from pathlib import Path

import pytest

V2 = Path(__file__).resolve().parents[1]
DEPLOY = V2 / "deploy"
SYSTEMD = DEPLOY / "systemd"
FAKE_DOCKER = Path(__file__).with_name("deploy_fake_docker.py")
URL = "http://unraid.probe.ts.net:3001/api/push/nur-fuer-den-test"


def unit(path: Path) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str  # type: ignore[assignment,method-assign]
    parser.read(path)
    return parser


def example_names() -> set[str]:
    lines = (DEPLOY / "monitoring.env.example").read_text().splitlines()
    return {line.split("=", 1)[0] for line in lines if line and not line.startswith("#")}


# --- Jeder Timer meldet ---


def heartbeat_of(service: Path) -> str:
    """Name, unter dem der Dienst seinen Herzschlag meldet (ExecStopPost)."""
    post = unit(service)["Service"]["ExecStopPost"]
    prefix = "/opt/psagent/v2/deploy/melden.sh "
    assert post.startswith(prefix), service.name
    name, unit_name = post.removeprefix(prefix).split()
    assert unit_name == "%n", service.name
    return name


def test_every_timer_reports_a_heartbeat_with_a_configured_address() -> None:
    timers = sorted(SYSTEMD.glob("psagent-*.timer"))
    assert len(timers) == 7
    names = set()
    for timer in timers:
        target = unit(timer)["Timer"]["Unit"]
        if target.startswith("psagent-befehl@"):
            assert heartbeat_of(SYSTEMD / "psagent-befehl@.service") == "%i"
            name = target.removeprefix("psagent-befehl@").removesuffix(".service")
        else:
            name = heartbeat_of(SYSTEMD / target)
        names.add(f"PSAGENT_PUSH_{name.upper()}")

    assert names == example_names()


def test_status_check_runs_hourly() -> None:
    timer = unit(SYSTEMD / "psagent-zustand.timer")["Timer"]
    service = unit(SYSTEMD / "psagent-zustand.service")["Service"]

    assert timer["OnCalendar"] == "*-*-* *:17:00"
    assert service["ExecStart"] == "/opt/psagent/v2/deploy/zustand.sh"


def test_example_holds_no_push_tokens() -> None:
    for line in (DEPLOY / "monitoring.env.example").read_text().splitlines():
        if line and not line.startswith("#"):
            assert line.endswith("/api/push/<token>"), line


@pytest.mark.parametrize("script", ["melden.sh", "zustand.sh"])
def test_scripts_are_valid_bash_and_executable(script: str) -> None:
    subprocess.run(["bash", "-n", str(DEPLOY / script)], check=True)  # noqa: S603, S607
    assert (DEPLOY / script).stat().st_mode & 0o111


# --- melden.sh ---


@pytest.fixture
def curl(tmp_path: Path) -> Path:
    """Falsches `curl`, das seine Argumente nach `tmp_path/curl.txt` schreibt."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "curl"
    fake.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$CURL_LOG"\n[ -z "$CURL_FAIL" ] || exit 7\n')
    fake.chmod(0o755)
    (tmp_path / "etc").mkdir()
    (tmp_path / "etc" / "monitoring.env").write_text(
        f"# Kommentar\nPSAGENT_PUSH_SICHERUNG={URL}\nPSAGENT_PUSH_KETTE_PRUEFEN={URL}-kette\n"
    )
    return tmp_path


def report(
    base: Path, *args: str, **env: str
) -> tuple[subprocess.CompletedProcess[str], dict[str, list[str]] | None]:
    log = base / "curl.txt"
    result = subprocess.run(  # noqa: S603
        ["bash", str(DEPLOY / "melden.sh"), *args],  # noqa: S607
        env={
            "PATH": f"{base / 'bin'}:/usr/bin:/bin",
            "PSAGENT_ETC": str(base / "etc"),
            "CURL_LOG": str(log),
            "CURL_FAIL": "",
            **env,
        },
        capture_output=True,
        text=True,
        check=False,
    )
    if not log.exists():
        return result, None
    args_sent = log.read_text().splitlines()
    # Uptime Kuma erwartet `status` und `msg` als Abfrageparameter (`-G`), nicht im Rumpf.
    assert "-G" in args_sent
    sent: dict[str, list[str]] = {"url": [args_sent[-1]]}
    for i, arg in enumerate(args_sent):
        if arg == "--data-urlencode":
            key, value = args_sent[i + 1].split("=", 1)
            sent[key] = [value]
    log.unlink()
    return result, sent


def test_success_reports_up(curl: Path) -> None:
    result, sent = report(curl, "sicherung", "psagent-sicherung.service", SERVICE_RESULT="success")

    assert result.returncode == 0
    assert sent == {"url": [URL], "status": ["up"], "msg": ["ok"]}


def test_failure_reports_down_at_once_without_details(curl: Path) -> None:
    """Sofort melden, nicht erst den ausbleibenden Herzschlag abwarten; nur Name und Ergebnis."""
    result, sent = report(
        curl,
        "kette_pruefen",
        "psagent-befehl@kette_pruefen.service",
        SERVICE_RESULT="exit-code",
        EXIT_STATUS="1",
    )

    assert result.returncode == 0
    assert sent is not None
    assert sent["url"] == [f"{URL}-kette"]
    assert sent["status"] == ["down"]
    assert sent["msg"] == [
        "Fehler (exit-code, Rückgabe 1); journalctl -u psagent-befehl@kette_pruefen.service"
    ]


def test_missing_address_reports_nothing(curl: Path) -> None:
    result, sent = report(curl, "zustand", SERVICE_RESULT="success")

    assert result.returncode == 0
    assert sent is None
    assert "PSAGENT_PUSH_ZUSTAND" in result.stderr


@pytest.mark.parametrize("name", ["../sicherung", "Sicherung", "", "sicherung;id"])
def test_unexpected_names_report_nothing(curl: Path, name: str) -> None:
    result, sent = report(curl, name)

    assert result.returncode == 0
    assert sent is None


def test_failed_report_does_not_fail_the_service(curl: Path) -> None:
    result, _ = report(curl, "sicherung", SERVICE_RESULT="success", CURL_FAIL="1")

    assert result.returncode == 0
    assert "fehlgeschlagen" in result.stderr


# --- zustand.sh ---


@pytest.fixture
def host(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE_DOCKER}" "$@"\n')
    docker.chmod(0o755)
    df = bin_dir / "df"
    df.write_text('#!/bin/sh\nprintf "Use%%\\n %s%%\\n" "$FAKE_DF"\n')
    df.chmod(0o755)
    return tmp_path


def status(base: Path, **env: str) -> subprocess.CompletedProcess[str]:
    log = base / "aufrufe.jsonl"
    log.touch()
    return subprocess.run(  # noqa: S603
        ["bash", str(DEPLOY / "zustand.sh")],  # noqa: S607
        env={
            "PATH": f"{base / 'bin'}:/usr/bin:/bin",
            "FAKE_LOG": str(log),
            "PSAGENT_ETC": str(base / "etc"),
            "PSAGENT_VAR": str(base),
            "FAKE_DF": "40",
            **env,
        },
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )


def test_status_is_fine_when_everything_is_healthy(host: Path) -> None:
    result = status(host)

    assert result.returncode == 0, result.stderr
    assert "alles in Ordnung" in result.stdout


@pytest.mark.parametrize(
    ("env", "reason"),
    [
        ({"FAKE_HEALTH": "psagent-app-1=unhealthy"}, "app: unhealthy"),
        ({"FAKE_HEALTH": "psagent-app-1="}, "app: läuft nicht"),
        ({"FAKE_HEALTH": "psagent-db-1=starting"}, "db: starting"),
        ({"FAKE_ARCHIV": "t|0"}, "WAL-Archiv: letzter Versuch gescheitert"),
        ({"FAKE_ARCHIV": "f|25"}, "WAL-Archiv: 25 Abschnitte warten auf Unraid"),
        ({"FAKE_ARCHIV": "Fehler"}, "WAL-Archiv: Zustand nicht lesbar"),
        ({"FAKE_DF": "90"}, "Platte: nur noch 10 % frei"),
    ],
)
def test_status_names_what_is_wrong(host: Path, env: dict[str, str], reason: str) -> None:
    result = status(host, **env)

    assert result.returncode == 1
    assert reason in result.stderr


def test_status_skips_the_archive_while_the_database_is_down(host: Path) -> None:
    result = status(host, FAKE_HEALTH="psagent-db-1=")

    assert result.returncode == 1
    assert "WAL-Archiv" not in result.stderr
    assert "pg_stat_archiver" not in (host / "aufrufe.jsonl").read_text()
