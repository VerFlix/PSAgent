"""Falsches `docker` für die Tests der Betriebsskripte (#23, #25).

Schreibt jeden Aufruf als JSON-Zeile nach `$FAKE_LOG` und antwortet wie `docker compose exec`
mit festen Ausgaben aus Umgebungsvariablen. Wie das echte `docker compose exec` und `run` liest
es dabei die Standardeingabe ganz mit; so fallen Schleifen auf, die ihre Liste verlieren.

- `FAKE_HEADS`, `FAKE_STORED_HEADS`, `FAKE_ARCHIVES`: Namen, durch Leerzeichen getrennt
- `FAKE_FAIL`: Aufrufe, die diesen Text enthalten, enden mit 1
- `FAKE_RUNNING`: Ausgabe von `docker compose ps -q`
- `FAKE_KETTE`: Rückgabewert von `kette_pruefen`
"""

import json
import os
import sys


def table(names: str) -> str:
    rows = [f"obj  5    2026-10-24 01:00:00 +0000 UTC {name}" for name in names.split()]
    return "\n".join(["type size last modified name", *rows]) + "\n"


def main() -> int:
    args = sys.argv[1:]
    joined = " ".join(args)
    received = b""
    if ("exec" in args or "run" in args) and not sys.stdin.isatty():
        received = sys.stdin.buffer.read()
    with open(os.environ["FAKE_LOG"], "a", encoding="utf-8") as log:
        entry = {"args": args, "stdin": received.decode("utf-8", "replace")}
        log.write(json.dumps(entry) + "\n")
    fail = os.environ.get("FAKE_FAIL", "")
    if fail and fail in joined:
        return 1
    if args[-2:] == ["ps", "-q"]:
        sys.stdout.write(os.environ.get("FAKE_RUNNING", ""))
    elif "pg_is_in_recovery()" in joined:
        sys.stdout.write("f\n")
    elif "pg_control_checkpoint()" in joined:
        sys.stdout.write("2\n")
    elif args[:1] == ["inspect"]:
        sys.stdout.write("healthy\n")
    elif "kette_pruefen" in args:
        return int(os.environ.get("FAKE_KETTE", "0"))
    elif "st get" in joined:
        sys.stdout.write("ARCHIV")
    elif args[-2:] == ["-1", "/var/lib/psagent/kettenkopf"]:
        sys.stdout.write("\n".join(os.environ.get("FAKE_HEADS", "").split()) + "\n")
    elif args[-3:] == ["st", "ls", "kettenkopf/"]:
        sys.stdout.write(table(os.environ.get("FAKE_STORED_HEADS", "")))
    elif args[-3:] == ["st", "ls", "dateien/"]:
        sys.stdout.write(table(os.environ.get("FAKE_ARCHIVES", "")))
    elif "tar" in args:
        sys.stdout.write("TAR-INHALT")
    elif "cat" in args:
        sys.stdout.write(f"INHALT {args[-1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
