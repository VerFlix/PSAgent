#!/usr/bin/env bash
# Verwaltungsbefehl von PSAgent im laufenden Container (#22), z. B.
#
#     sudo ./manage.sh loeschfristen --probelauf
#     sudo ./manage.sh createsuperuser
#
# Die Timer (systemd/psagent-befehl@.service) rufen dieses Skript ohne Terminal auf.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
tty=(-T)
if [[ -t 0 && -t 1 ]]; then
    tty=()
fi
exec docker compose --project-directory "$here" --env-file /etc/psagent/image.env \
    exec "${tty[@]}" app python manage.py "$@"
