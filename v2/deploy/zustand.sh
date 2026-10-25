#!/usr/bin/env bash
# Stündlicher Zustand des Pi für das Monitoring (#24), gestartet von psagent-zustand.timer:
#
#   - App und Datenbank melden sich gesund (Healthcheck in compose.yaml)
#   - das WAL-Archiv nach Unraid läuft: letzter Versuch nicht gescheitert, kein Rückstau
#   - auf /var/lib/psagent ist genug Platz frei
#
#     sudo ./zustand.sh
#
# Endet mit 1, wenn etwas nicht stimmt; die Gründe stehen im Journal. Die Meldung an Uptime Kuma
# macht melden.sh (ExecStopPost im Timer-Dienst).
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
etc="${PSAGENT_ETC:-/etc/psagent}"
var="${PSAGENT_VAR:-/var/lib/psagent}"
min_free="${PSAGENT_MIN_FREI_PROZENT:-15}"
max_waiting="${PSAGENT_MAX_WAL_WARTEND:-10}"
compose=(docker compose --project-directory "$here" --env-file "$etc/image.env")

problems=()

for service in app db; do
    health="$(docker inspect --format '{{.State.Health.Status}}' "psagent-$service-1" 2>/dev/null)" \
        || health="läuft nicht"
    [[ "$health" == healthy ]] || problems+=("$service: $health")
done

if [[ ! " ${problems[*]} " =~ " db: " ]]; then
    # Gescheitert ist das Archiv, wenn der letzte Fehlversuch nach dem letzten Erfolg liegt.
    archive="$("${compose[@]}" exec -T -u postgres db psql -Atc "
        select coalesce(last_failed_time > coalesce(last_archived_time, 'epoch'), false),
               (select count(*) from pg_ls_archive_statusdir() where name like '%.ready')
        from pg_stat_archiver" </dev/null)" || archive=""
    if [[ "$archive" =~ ^([tf])\|([0-9]+)$ ]]; then
        [[ "${BASH_REMATCH[1]}" == t ]] && problems+=("WAL-Archiv: letzter Versuch gescheitert")
        ((BASH_REMATCH[2] <= max_waiting)) \
            || problems+=("WAL-Archiv: ${BASH_REMATCH[2]} Abschnitte warten auf Unraid")
    else
        problems+=("WAL-Archiv: Zustand nicht lesbar")
    fi
fi

used="$(df --output=pcent "$var" 2>/dev/null | tail -n 1 | tr -dc '0-9')" || used=""
if [[ -z "$used" ]]; then
    problems+=("Platte: Belegung von $var nicht lesbar")
elif ((100 - used < min_free)); then
    problems+=("Platte: nur noch $((100 - used)) % frei auf $var")
fi

if ((${#problems[@]})); then
    printf 'Zustand: %s\n' "${problems[@]}" >&2
    exit 1
fi
echo "Zustand: alles in Ordnung."
