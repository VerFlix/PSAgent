#!/usr/bin/env bash
# Herzschlag an Uptime Kuma auf Unraid (#24). Die Timer rufen es nach jedem Lauf auf:
#
#     ExecStopPost=/opt/psagent/v2/deploy/melden.sh <name> %n
#
# systemd übergibt das Ergebnis in $SERVICE_RESULT: „success“ meldet status=up, alles andere
# sofort status=down. Bleibt der Herzschlag ganz aus (Pi oder Timer aus), schlägt Uptime Kuma nach
# dem eingestellten Abstand Alarm. Von Hand zum Ausprobieren:
#
#     sudo ./melden.sh sicherung                          # meldet „ok“
#     sudo SERVICE_RESULT=exit-code ./melden.sh sicherung # meldet einen Fehler
#
# Die Push-Adressen stehen in /etc/psagent/monitoring.env (Vorlage monitoring.env.example); fehlt
# eine, passiert nichts. Die Meldung nennt nur Name und Ergebnis, nie Personendaten: Sie geht über
# Uptime Kuma an ntfy. Scheitert die Meldung, steht das im Journal; der Lauf selbst zählt trotzdem.
set -uo pipefail

name="${1:-}"
unit="${2:-psagent-$name}"
if [[ ! "$name" =~ ^[a-z_]+$ ]]; then
    echo "melden.sh: unbekannter Name „$name“; keine Meldung." >&2
    exit 0
fi
variable="PSAGENT_PUSH_${name^^}"
config="${PSAGENT_ETC:-/etc/psagent}/monitoring.env"
url="$(grep -E "^${variable}=" "$config" 2>/dev/null | tail -n 1 | cut -d= -f2-)"
if [[ -z "$url" ]]; then
    echo "melden.sh: $variable fehlt in $config; keine Meldung." >&2
    exit 0
fi

result="${SERVICE_RESULT:-success}"
if [[ "$result" == success ]]; then
    status=up
    message="ok"
else
    status=down
    message="Fehler ($result, Rückgabe ${EXIT_STATUS:-?}); journalctl -u $unit"
fi
curl -fsS --max-time 10 --retry 2 -o /dev/null -G \
    --data-urlencode "status=$status" --data-urlencode "msg=$message" "$url" \
    || echo "melden.sh: Meldung an Uptime Kuma fehlgeschlagen ($variable)." >&2
exit 0
