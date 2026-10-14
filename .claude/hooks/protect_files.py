#!/usr/bin/env python3
"""PreToolUse-Hook: blockiert Änderungen an geschützten Dateien.

Geschützt sind Schlüssel- und Geheimnisdateien, Datenbankdateien, das Altsystem und
eingecheckte Migrationen (siehe CLAUDE.md, Harte Regeln). Exit-Code 2 blockiert den
Werkzeugaufruf; der Text auf stderr geht als Begründung an Claude zurück.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

SECRET_FILE = re.compile(
    r"(^|/)(\.env(\..+)?|.+\.(pem|key|p12|pfx|gpg|asc|kdbx)|id_(rsa|ed25519|ecdsa)[^/]*)$"
)
SECRET_TEMPLATE = re.compile(r"(^|/)\.env\.example$")
DATABASE_FILE = re.compile(r"\.(db|sqlite3?)$")
LEGACY = ("psa_gui.py", "verleih_gui.py", "generate_PDF.py", "Typst_Vorlagen/")
MIGRATION = re.compile(r"(^|/)migrations/\d{4}_[^/]+\.py$")


def block(reason: str) -> None:
    print(f"Blockiert durch .claude/hooks/protect_files.py: {reason}", file=sys.stderr)
    sys.exit(2)


def is_tracked(project_dir: Path, rel_path: str) -> bool:
    try:
        result = subprocess.run(  # noqa: S603
            ["git", "ls-files", "--error-unmatch", "--", rel_path],  # noqa: S607
            cwd=project_dir,
            capture_output=True,
            check=False,
        )
    except OSError:
        return False
    return result.returncode == 0


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        return
    tool_input = payload.get("tool_input") or {}
    raw_path = tool_input.get("file_path") or tool_input.get("notebook_path")
    if not raw_path:
        return

    project_dir = Path(os.environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd") or ".").resolve()
    path = Path(raw_path)
    if not path.is_absolute():
        path = project_dir / path
    path = path.resolve()
    try:
        rel = path.relative_to(project_dir).as_posix()
    except ValueError:
        return  # außerhalb des Repos: nicht Sache dieses Hooks

    if SECRET_FILE.search(rel) and not SECRET_TEMPLATE.search(rel):
        block(
            f"{rel} sieht nach einer Schlüssel- oder Geheimnisdatei aus. Geheimnisse gehören "
            "nicht ins Repo; nur Vorlagen wie .env.example sind erlaubt."
        )
    if DATABASE_FILE.search(rel):
        block(
            f"{rel} ist eine Datenbankdatei. Echte Daten werden nie geöffnet oder geändert; "
            "Testdaten kommen aus dem Testdaten-Generator."
        )
    if any(rel == item or rel.startswith(item) for item in LEGACY):
        block(f"{rel} gehört zum Altsystem und wird nicht geändert (CLAUDE.md, Regel 7).")
    if MIGRATION.search(rel) and is_tracked(project_dir, rel):
        block(
            f"{rel} ist eine eingecheckte Migration. Lege stattdessen eine neue Migration an "
            "(CLAUDE.md, Regel 4)."
        )


if __name__ == "__main__":
    main()
