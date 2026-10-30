#!/usr/bin/env bash
# Version der Images einspielen (#22): holen, Datenbank migrieren, starten, prüfen.
#
#     sudo ./update.sh sha-<commit>
#
# Die vorige Version steht danach in /etc/psagent/image.env.vorher; zurück geht es mit
# `sudo ./update.sh <vorige Version>` (Migrationen laufen nur vorwärts, siehe Runbook). Ältere
# Images löscht das Skript, wenn die neue Version läuft (kleine SSD); die vorige bleibt.
set -euo pipefail

tag="${1:?Version angeben, z. B. sha-<commit> (siehe GitHub, Paket psagent)}"
if [[ ! "$tag" =~ ^(sha-[0-9a-f]{40}|v2)$ ]]; then
    echo "Unbekanntes Format: $tag (erwartet sha-<40 Hexziffern> oder v2)" >&2
    exit 2
fi

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
image_env="${PSAGENT_ETC:-/etc/psagent}/image.env"
compose=(docker compose --project-directory "$here" --env-file "$image_env")

if [[ -f "$image_env" ]]; then
    cp "$image_env" "$image_env.vorher"
fi
umask 077
printf 'PSAGENT_IMAGE_TAG=%s\n' "$tag" > "$image_env"

"${compose[@]}" pull app db
"${compose[@]}" up -d db
"${compose[@]}" run --rm app python manage.py migrate --noinput
"${compose[@]}" up -d app

healthy=0
for _ in $(seq 1 30); do
    if [[ "$(docker inspect --format '{{.State.Health.Status}}' psagent-app-1 2>/dev/null)" == healthy ]]; then
        healthy=1
        break
    fi
    sleep 5
done
if ((!healthy)); then
    echo "PSAgent $tag meldet sich nicht gesund; Protokoll: docker compose logs app" >&2
    exit 1
fi

# Erst jetzt aufräumen: Die laufende und die vorige Version bleiben für ein Zurück.
previous="$(sed -n 's/^PSAGENT_IMAGE_TAG=//p' "$image_env.vorher" 2>/dev/null || true)"
for repo in ghcr.io/verflix/psagent ghcr.io/verflix/psagent-db; do
    while IFS= read -r old; do
        [[ -z "$old" || "$old" == "<none>" || "$old" == "$tag" || "$old" == "$previous" ]] && continue
        docker image rm "$repo:$old" >/dev/null || echo "Altes Image $repo:$old bleibt." >&2
    done < <(docker image ls "$repo" --format '{{.Tag}}')
done
docker image prune -f >/dev/null || true
echo "PSAgent $tag läuft."
