#!/usr/bin/env bash
# SessionStart-Hook: macht Cloud-Sitzungen von Claude Code testfähig.
#
# Läuft nur, wenn CLAUDE_CODE_REMOTE=true gesetzt ist; lokal tut das Skript nichts (dort gilt
# v2/README.md, „Lokal starten“). Installiert die Abhängigkeiten von v2/ genau nach uv.lock,
# startet bei Bedarf den Docker-Daemon und dann PostgreSQL 17 aus v2/compose.yaml. Das Skript
# wartet, bis die Datenbank Verbindungen annimmt. Mehrfaches Ausführen ist unschädlich
# (startup und resume).
set -euo pipefail

if [[ "${CLAUDE_CODE_REMOTE:-}" != "true" ]]; then
  exit 0
fi

PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
LOG_DIR="${TMPDIR:-/tmp}/psagent-session-start"
STEP_LOG="$LOG_DIR/session-start.log"
DOCKERD_LOG="$LOG_DIR/dockerd.log"
mkdir -p "$LOG_DIR"
: >"$STEP_LOG"

fail() {
  {
    echo "SessionStart-Hook (PSAgent v2): $1"
    echo "Protokolle: $STEP_LOG und $DOCKERD_LOG"
    tail -n 20 "$STEP_LOG" || true
  } >&2
  exit 1
}

pg_ready() {
  pg_isready -q -h 127.0.0.1 -p 5432 -U psagent -d psagent
}

cd "$PROJECT_DIR/v2"

# 1. Python-Abhängigkeiten genau nach uv.lock, wie in der CI.
command -v uv >/dev/null || fail "uv ist nicht installiert."
uv sync --locked >>"$STEP_LOG" 2>&1 || fail "uv sync --locked ist fehlgeschlagen."

# 1b. SoftHSM und OpenSC für die Tests des Signier-Tools (#38), wie in der CI.
if ! command -v softhsm2-util >/dev/null || ! command -v pkcs11-tool >/dev/null; then
  { apt-get install -y -q softhsm2 opensc || { apt-get update -q && apt-get install -y -q softhsm2 opensc; }; } \
    >>"$STEP_LOG" 2>&1 || fail "softhsm2/opensc ließen sich nicht installieren."
fi

# 2. Docker-Daemon: läuft in Cloud-Sitzungen nicht von selbst.
command -v docker >/dev/null || fail "docker ist nicht installiert."
if ! docker info >/dev/null 2>&1; then
  command -v dockerd >/dev/null || fail "dockerd ist nicht installiert."
  # setsid löst den Daemon vom Hook-Prozess, damit er nach dem Hook weiterläuft.
  setsid dockerd >>"$DOCKERD_LOG" 2>&1 </dev/null &
  for _ in $(seq 1 60); do
    docker info >/dev/null 2>&1 && break
    sleep 1
  done
  docker info >/dev/null 2>&1 || fail "Der Docker-Daemon antwortet nach 60 s nicht."
fi

# 3. PostgreSQL 17 starten; --wait wartet auf den Healthcheck aus compose.yaml.
docker compose up -d --wait --wait-timeout 180 db >>"$STEP_LOG" 2>&1 ||
  fail "Der PostgreSQL-Container ist nicht gesund geworden."

# 4. Von außen prüfen, ob die Datenbank auf 127.0.0.1:5432 antwortet (wie für pytest).
if command -v pg_isready >/dev/null; then
  for _ in $(seq 1 30); do
    pg_ready && break
    sleep 1
  done
  pg_ready || fail "PostgreSQL antwortet nicht auf 127.0.0.1:5432."
fi

version="$(docker compose exec -T db psql -U psagent -d psagent -tAc 'show server_version' 2>/dev/null || true)"
echo "PSAgent v2: Abhängigkeiten installiert, PostgreSQL ${version:-17} läuft auf 127.0.0.1:5432."
