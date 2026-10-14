#!/usr/bin/env python3
"""PostToolUse-Hook: formatiert und prüft geänderte Python-Dateien in v2/ mit ruff.

Behebbare Befunde werden direkt korrigiert. Bleibt danach etwas übrig, endet der Hook mit
Exit-Code 2 und gibt die Befunde an Claude zurück. Ohne ruff (z. B. vor `uv sync`) bleibt
der Hook still.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def find_ruff(v2_dir: Path) -> str | None:
    local = v2_dir / ".venv" / "bin" / "ruff"
    if local.exists():
        return str(local)
    return shutil.which("ruff")


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        return
    raw_path = (payload.get("tool_input") or {}).get("file_path")
    if not raw_path or not raw_path.endswith(".py"):
        return

    project_dir = Path(os.environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd") or ".").resolve()
    v2_dir = project_dir / "v2"
    path = Path(raw_path)
    if not path.is_absolute():
        path = project_dir / path
    path = path.resolve()
    if not path.is_file() or not path.is_relative_to(v2_dir):
        return

    ruff = find_ruff(v2_dir)
    if ruff is None:
        return

    # Erst behebbare Befunde korrigieren, dann formatieren, dann den Rest melden.
    for args in (["check", "--fix", "--quiet"], ["format", "--quiet"]):
        subprocess.run(  # noqa: S603
            [ruff, *args, str(path)], cwd=v2_dir, capture_output=True, check=False
        )
    result = subprocess.run(  # noqa: S603
        [ruff, "check", "--quiet", str(path)],
        cwd=v2_dir,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        print(
            f"ruff meldet Befunde in {path.relative_to(project_dir)}:\n"
            f"{result.stdout}{result.stderr}",
            file=sys.stderr,
        )
        sys.exit(2)


if __name__ == "__main__":
    main()
