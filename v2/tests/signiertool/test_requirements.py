"""Feste Versionen und Prüfsummen für das Signier-Tool auf dem PC (#105).

`signiertool/requirements.txt` kommt aus `uv.lock` und muss dazu passen: Was CI und Tests prüfen,
landet so auch auf dem Signier-PC. Das Installationsskript installiert mit `--require-hashes`;
ein ausgetauschtes Paket gleicher Version fiele damit auf. Ändert sich `uv.lock` (etwa durch
Dependabot), schlägt der erste Test fehl, bis die Liste neu erzeugt ist (Befehl siehe EXPORT).
"""

import re
import shutil
import subprocess
from pathlib import Path

V2 = Path(__file__).resolve().parents[2]
REQUIREMENTS = V2 / "signiertool" / "requirements.txt"
SCRIPT = V2 / "signiertool" / "installieren.ps1"
EXPORT = [
    "uv",
    "export",
    "--frozen",
    "--package",
    "psagent-signieren",
    "--no-dev",
    "--no-emit-workspace",
    "--format",
    "requirements-txt",
    "-o",
    "signiertool/requirements.txt",
]


def test_requirements_match_the_lock() -> None:
    uv = shutil.which("uv")
    assert uv is not None, "uv fehlt; die Tests laufen mit `uv run pytest`."

    exported = subprocess.run(  # noqa: S603 – fester Befehl ohne Eingaben
        [uv, *EXPORT[1:-2], "--no-header"],
        cwd=V2,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    stored = REQUIREMENTS.read_text()
    body = "\n".join(line for line in stored.splitlines() if not line.startswith("#")) + "\n"

    assert body.strip() == exported.strip(), (
        "signiertool/requirements.txt passt nicht zu uv.lock. Neu erzeugen mit: " + " ".join(EXPORT)
    )


def test_every_package_is_pinned_with_a_hash() -> None:
    entries = re.split(r"\n(?=\S)", REQUIREMENTS.read_text())
    packages = [entry for entry in entries if entry and not entry.startswith("#")]

    assert len(packages) >= 10
    for entry in packages:
        assert re.match(r"^[a-z0-9._-]+==[^\s]+", entry), entry.splitlines()[0]
        assert "--hash=sha256:" in entry, entry.splitlines()[0]
    names = {entry.split("==")[0] for entry in packages}
    assert {"pyhanko", "python-pkcs11", "pypdf", "cryptography"} <= names


def test_install_script_requires_hashes() -> None:
    """Das Skript läuft unter Windows (erprobt lokal); hier nur, dass es die Liste erzwingt."""
    lines = SCRIPT.read_text(encoding="utf-8").splitlines()
    script = "\n".join(line for line in lines if not line.lstrip().startswith("#"))

    installs = [line for line in script.splitlines() if '"pip", "install"' in line]
    assert installs and all('"--require-hashes"' in line for line in installs), installs
    assert "requirements.txt" in script
    assert "uv tool install" not in script  # prüft keine Prüfsummen (#105)
    # Das Tool selbst wird kopiert, nicht gebaut: kein Build-Werkzeug ohne Prüfsumme.
    assert not any("$Source" in line for line in installs), installs
    assert "-m psagent_signieren.cli" in script
