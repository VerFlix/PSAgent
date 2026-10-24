#!/usr/bin/env bash
# Version des Images einspielen (#22): holen, Datenbank migrieren, starten, prüfen.
#
#     sudo ./update.sh sha-<commit>
#
# Die vorige Version steht danach in /etc/psagent/image.env.vorher; zurück geht es mit
# `sudo ./update.sh <vorige Version>` (Migrationen laufen nur vorwärts, siehe Runbook).
set -euo pipefail

tag="${1:?Version angeben, z. B. sha-<commit> (siehe GitHub, Paket psagent)}"
if [[ ! "$tag" =~ ^(sha-[0-9a-f]{40}|v2)$ ]]; then
    echo "Unbekanntes Format: $tag (erwartet sha-<40 Hexziffern> oder v2)" >&2
    exit 2
fi

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
image_env=/etc/psagent/image.env
compose=(docker compose --project-directory "$here" --env-file "$image_env")

if [[ -f "$image_env" ]]; then
    cp "$image_env" "$image_env.vorher"
fi
umask 077
printf 'PSAGENT_IMAGE_TAG=%s\n' "$tag" > "$image_env"

"${compose[@]}" pull app
"${compose[@]}" up -d db
"${compose[@]}" run --rm app python manage.py migrate --noinput
"${compose[@]}" up -d app

for _ in $(seq 1 30); do
    if [[ "$(docker inspect --format '{{.State.Health.Status}}' psagent-app-1 2>/dev/null)" == healthy ]]; then
        echo "PSAgent $tag läuft."
        exit 0
    fi
    sleep 5
done
echo "PSAgent $tag meldet sich nicht gesund; Protokoll: docker compose logs app" >&2
exit 1
