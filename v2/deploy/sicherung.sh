#!/usr/bin/env bash
# Nächtliche Sicherung nach Unraid (#23), gestartet von systemd/psagent-sicherung.timer:
#
#   1. Vollsicherung der Datenbank (WAL-G, verschlüsselt); WAL-Abschnitte gehen laufend raus.
#   2. Dateien (GAL, Scans, Notfallmappen) als ein tar-Archiv, verschlüsselt, unter dateien/.
#   3. Neue Kettenköpfe unverschlüsselt unter kettenkopf/ (nur Prüfsummen, für den Zeugen).
#   4. Aufräumen: Es bleiben die letzten 30 Vollsicherungen samt WAL und die letzten 30
#      Datei-Archive. Ältere Stände halten die Snapshots auf Unraid, die der Pi nicht löschen kann.
#
#     sudo ./sicherung.sh
#
# Verschlüsselt wird mit dem öffentlichen Schlüssel; den privaten hat der Pi nicht. Scheitert ein
# Schritt, laufen die übrigen weiter (aufgeräumt wird dann nicht), und das Skript endet mit 1.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
etc="${PSAGENT_ETC:-/etc/psagent}"
keep="${PSAGENT_SICHERUNG_BEHALTEN:-30}"
compose=(docker compose --project-directory "$here" --env-file "$etc/image.env")
db=("${compose[@]}" exec -T -u postgres db)
app=("${compose[@]}" exec -T app)

if [[ -e "$etc/sicherung/probe" ]]; then
    echo "Probebetrieb ($etc/sicherung/probe): keine Sicherung." >&2
    exit 0
fi

failed=0
fail() {
    echo "FEHLER: $1" >&2
    failed=1
}

objects() {
    # Namen der Objekte in einem Ordner der Sicherung; leer, wenn es den Ordner noch nicht gibt.
    { "${db[@]}" wal-g st ls "$1/" 2>/dev/null || true; } | awk '$1 == "obj" { print $NF }'
}

stamp="$(date -u +%Y-%m-%dT%H%M%SZ)"

echo "1/4 Vollsicherung der Datenbank"
database=0
"${db[@]}" wal-g backup-push || { fail "Vollsicherung"; database=1; }

echo "2/4 Dateien"
files=0
"${app[@]}" tar --numeric-owner -C /var/lib/psagent -cf - media \
    | "${db[@]}" wal-g st put -s "dateien/dateien-$stamp.tar" || { fail "Dateien"; files=1; }

echo "3/4 Kettenköpfe"
stored="$(objects kettenkopf)"
heads="$("${app[@]}" ls -1 /var/lib/psagent/kettenkopf </dev/null)" || fail "Kettenköpfe auflisten"
# Liste über Deskriptor 3: `docker compose exec` liest sonst die übrigen Namen von stdin mit.
while IFS= read -r name <&3; do
    [[ -n "$name" ]] || continue
    if [[ ! "$name" =~ ^[A-Za-z0-9._-]+$ ]]; then
        fail "Kettenkopf-Datei mit unerwartetem Namen übersprungen: $name"
        continue
    fi
    if grep -qxF "$name" <<<"$stored"; then
        continue
    fi
    "${app[@]}" cat "/var/lib/psagent/kettenkopf/$name" </dev/null \
        | "${db[@]}" wal-g st put -s --no-encrypt --no-compress "kettenkopf/$name" \
        || fail "Kettenkopf $name"
done 3<<<"$heads"

echo "4/4 Aufräumen"
if ((database == 0)); then
    "${db[@]}" wal-g delete retain FULL "$keep" --confirm || fail "Alte Vollsicherungen löschen"
else
    echo "Übersprungen: Vollsicherung fehlgeschlagen." >&2
fi
if ((files == 0)); then
    mapfile -t archives < <(objects dateien | grep -E '^dateien-.*\.tar' | sort)
    for ((i = 0; i < ${#archives[@]} - keep; i++)); do
        "${db[@]}" wal-g st rm "dateien/${archives[i]}" || fail "Altes Archiv ${archives[i]} löschen"
    done
else
    echo "Übersprungen: Dateien fehlgeschlagen." >&2
fi

if ((failed)); then
    echo "Sicherung unvollständig; Protokoll: journalctl -u psagent-sicherung" >&2
    exit 1
fi
echo "Sicherung $stamp vollständig."
