#!/usr/bin/env bash
# Wiederherstellung aus der Sicherung auf Unraid (#25).
#
#     sudo ./wiederherstellen.sh --probe     <privater-schluessel.asc> [--kopf <Nummer>:<Hash>]…
#     sudo ./wiederherstellen.sh --ernstfall <privater-schluessel.asc> [--kopf <Nummer>:<Hash>]…
#
# --probe      Wiederherstellungsprobe auf einem anderen Gerät (Ersatzgerät). Die Datenbank
#              schreibt danach nichts in die echte Sicherung (Marke /etc/psagent/sicherung/probe).
# --ernstfall  Dieses Gerät übernimmt den Betrieb. Der alte Server muss aus und aus dem Tailnet
#              entfernt sein, sonst schreiben zwei Server in dieselbe Sicherung.
# --kopf       Kettenkopf aus der letzten Zeugen-Mail; `kette_pruefen` prüft, ob er noch passt.
#
# Voraussetzungen: docs/runbooks/server-einrichten.md, Schritte 1–5 (Docker, Tailscale,
# /etc/psagent mit psagent.env, image.env und sicherung/), leere Ordner postgres/ und media/ unter
# /var/lib/psagent. Nichts wird überschrieben. Die Passphrase fragt das Skript ab (oder aus
# WALG_PGP_KEY_PASSPHRASE); der private Schlüssel bleibt nur in kurzlebigen Containern.
#
# Ablauf: Schlüssel prüfen, neueste Vollsicherung holen, WAL bis zum letzten Stand nachspielen,
# Dateien holen, migrieren, starten, Hash-Kette prüfen. Rückgabewert wie `kette_pruefen`
# (0 unverändert, 1 verändert, 2 nicht prüfbar); andere Fehler enden mit 1.
set -euo pipefail

usage() {
    sed -n '3,5p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,4\}//' >&2
    exit 2
}
die() {
    echo "Abbruch: $1" >&2
    exit 1
}

mode="${1:-}"
[[ "$mode" == --probe || "$mode" == --ernstfall ]] || usage
key="${2:-}"
[[ -n "$key" ]] || usage
shift 2
heads=()
while (($#)); do
    [[ "$1" == --kopf && -n "${2:-}" ]] || usage
    [[ "$2" =~ ^[0-9]+:[0-9a-f]{64}$ ]] || die "--kopf erwartet <Nummer>:<Hash>, nicht $2"
    heads+=(--kopf "$2")
    shift 2
done

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
etc="${PSAGENT_ETC:-/etc/psagent}"
var="${PSAGENT_VAR:-/var/lib/psagent}"
marker="$etc/sicherung/probe"
recovery=psagent-wiederherstellung
compose=(docker compose --project-directory "$here" --env-file "$etc/image.env")
started="$(date +%s)"

# --- Vorbedingungen: nichts überschreiben ---

[[ -f "$key" && -r "$key" ]] || die "Schlüsseldatei nicht lesbar: $key"
key="$(cd "$(dirname "$key")" && pwd)/$(basename "$key")"
for file in image.env psagent.env sicherung/public.asc; do
    [[ -f "$etc/$file" ]] || die "$etc/$file fehlt (Runbook server-einrichten.md, Schritt 5)."
done
for dir in postgres media; do
    [[ -d "$var/$dir" ]] || die "$var/$dir fehlt (Runbook server-einrichten.md, Schritt 5)."
    [[ -z "$(ls -A "$var/$dir")" ]] || die "$var/$dir ist nicht leer; es wird nichts überschrieben."
done
[[ -z "$("${compose[@]}" ps -q)" ]] || die "PSAgent läuft noch; erst: docker compose down"
if [[ "$mode" == --probe ]]; then
    printf '%s\n' "Wiederherstellungsprobe seit $(date -Is): keine Sicherung von diesem Gerät." \
        "Erst nach dem Aufräumen der Probe löschen (docs/runbooks/sicherung.md)." >"$marker"
elif [[ -e "$marker" ]]; then
    die "$marker gibt es; das ist ein Probegerät. Für den Ernstfall die Marke bewusst löschen."
fi

if [[ -z "${WALG_PGP_KEY_PASSPHRASE:-}" ]]; then
    read -rsp "Passphrase des privaten Sicherungsschlüssels: " WALG_PGP_KEY_PASSPHRASE </dev/tty
    echo >&2
fi
export WALG_PGP_KEY_PASSPHRASE

# Kurzlebiger Container mit dem privaten Schlüssel; Rest der Zeile ist ein bash-Skript.
with_key=("${compose[@]}" run --rm --no-deps -T -e WALG_PGP_KEY_PASSPHRASE
    -e WALG_PGP_KEY_PATH=/schluessel/privat.asc -v "$key:/schluessel/privat.asc:ro"
    --entrypoint bash db -c)
cleanup() {
    docker rm -f "$recovery" >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "1/6 Schlüssel und Passphrase prüfen"
# WAL-G versucht es bei falscher Passphrase 15-mal im Minutentakt; gpg sagt es sofort.
"${with_key[@]}" 'set -e
export GNUPGHOME="$(mktemp -d)"
gpg --batch --quiet --import /schluessel/privat.asc
echo probe | gpg --batch --quiet --trust-model always --encrypt --default-recipient-self \
    | gpg --batch --quiet --pinentry-mode loopback --passphrase-fd 3 --decrypt \
        3<<<"$WALG_PGP_KEY_PASSPHRASE" >/dev/null' \
    || die "Schlüssel oder Passphrase passen nicht."

echo "2/6 Neueste Vollsicherung holen"
"${with_key[@]}" 'set -e
wal-g backup-fetch "$PGDATA" LATEST
touch "$PGDATA/recovery.signal"
chown -R postgres:postgres "$PGDATA"
chmod 700 "$PGDATA"'

echo "3/6 WAL bis zum letzten Stand nachspielen"
"${compose[@]}" run -d --name "$recovery" --no-deps -e WALG_PGP_KEY_PASSPHRASE \
    -e WALG_PGP_KEY_PATH=/tmp/privat.asc -v "$key:/schluessel/privat.asc:ro" \
    --entrypoint bash db -c 'install -m 600 -o postgres /schluessel/privat.asc /tmp/privat.asc
exec docker-entrypoint.sh postgres -c archive_mode=off -c "restore_command=wal-g wal-fetch %f %p"' \
    >/dev/null
for ((i = 0; ; i++)); do
    state="$(docker exec -u postgres "$recovery" psql -Atc 'select pg_is_in_recovery()' 2>/dev/null)" \
        || state=""
    [[ "$state" == f ]] && break
    ((i < 360)) || die "Nachspielen dauert über 30 Minuten; Protokoll: docker logs $recovery"
    sleep 5
done
docker stop "$recovery" >/dev/null
cleanup

echo "4/6 Dateien holen"
archive="$("${compose[@]}" run --rm --no-deps -T db wal-g st ls dateien/ 2>/dev/null \
    | awk '$1 == "obj" { print $NF }' | grep -E '^dateien-.*\.tar' | sort | tail -n 1 || true)"
if [[ -n "$archive" ]]; then
    "${with_key[@]}" 'wal-g st get "dateien/$1" /tmp/dateien.tar >&2 && cat /tmp/dateien.tar' \
        _ "$archive" \
        | "${compose[@]}" run --rm --no-deps -T --entrypoint tar app -C /var/lib/psagent -xf -
    echo "Dateien aus $archive"
else
    echo "Kein Datei-Archiv in der Sicherung; weiter ohne Dateien." >&2
fi

echo "5/6 Migrieren und starten"
unset WALG_PGP_KEY_PASSPHRASE
"${compose[@]}" up -d db
"${compose[@]}" run --rm app python manage.py migrate --noinput
"${compose[@]}" up -d app
for ((i = 0; ; i++)); do
    health="$(docker inspect --format '{{.State.Health.Status}}' psagent-app-1 2>/dev/null)" \
        || health=""
    [[ "$health" == healthy ]] && break
    ((i < 60)) || die "App meldet sich nicht gesund; Protokoll: docker compose logs app"
    sleep 5
done

if [[ "$mode" == --ernstfall ]]; then
    # Die neue Zeitlinie in die Sicherung, dann sofort eine frische Vollsicherung.
    timeline="$("${compose[@]}" exec -T -u postgres db \
        psql -Atc 'select timeline_id from pg_control_checkpoint()')"
    "${compose[@]}" exec -T -u postgres db bash -c \
        'wal-g wal-push "$PGDATA/pg_wal/$(printf %08X "$1").history"' _ "$timeline"
    "$here/sicherung.sh" || echo "Frische Sicherung unvollständig; bitte prüfen." >&2
fi

echo "6/6 Hash-Kette prüfen"
code=0
"${compose[@]}" exec -T app python manage.py kette_pruefen "${heads[@]}" || code=$?

minutes=$((($(date +%s) - started + 59) / 60))
echo "Wiederhergestellt in etwa $minutes Minuten; kette_pruefen: $code."
if [[ "$mode" == --probe ]]; then
    echo "Probe: keine Timer einschalten. Aufräumen: docs/runbooks/sicherung.md, Abschnitt Probe."
fi
exit "$code"
