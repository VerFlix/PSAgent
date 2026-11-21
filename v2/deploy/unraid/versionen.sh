#!/usr/bin/env bash
# Tägliche Versionsstände der Sicherung auf Unraid (#23), statt Snapshots auf XFS-Platten.
#
# Der Server schreibt per SFTP in QUELLE und löscht dort Stände, die älter als 30 Vollsicherungen
# sind. Dieses Skript läuft auf Unraid als root (Plugin „User Scripts“, täglich nach der
# Sicherung) und kopiert QUELLE nach ZIEL/<JJJJ-MM-TT>. ZIEL gehört root und kann der
# SFTP-Benutzer weder sehen noch ändern. Dateien, die seit dem Vortag gleich sind, werden
# Hardlinks auf den Stand vom Vortag und kosten keinen Platz. Mit QUELLE teilt ZIEL keine Datei.
#
# Aufbewahrt werden die Stände der letzten 30 Tage und je Monat der erste Stand, 12 Monate lang.
#
#   bash versionen.sh /mnt/user/psagent-sicherung /mnt/disk1/psagent-versionen
#
# ZIEL muss auf einer einzelnen Platte liegen (/mnt/diskN/… oder ein Pool), nicht unter
# /mnt/user: Hardlinks gehen nur innerhalb einer Platte.
#
# Aufbewahrung ändern: PSAGENT_VERSIONEN_TAGE (30) und PSAGENT_VERSIONEN_MONATE (12). Bei einem
# Vorfall PSAGENT_VERSIONEN_TAGE=3650, dann löscht es nichts (`vorfall-kette.md`).
# Für Tests: PSAGENT_HEUTE (Datum) und PSAGENT_UNRAID_NOTIFY (Meldeskript von Unraid).
set -euo pipefail
shopt -s nullglob

notify="${PSAGENT_UNRAID_NOTIFY:-/usr/local/emhttp/webGui/scripts/notify}"

fehler() {
    echo "FEHLER: $1" >&2
    if [[ -x "$notify" ]]; then
        "$notify" -e "PSAgent-Sicherung" -s "Versionsstand fehlgeschlagen" -d "$1" -i alert || true
    fi
    exit 1
}

if (($# != 2)); then
    echo "Aufruf: versionen.sh QUELLE ZIEL" >&2
    exit 2
fi
quelle="${1%/}"
ziel="${2%/}"
tage="${PSAGENT_VERSIONEN_TAGE:-30}"
monate="${PSAGENT_VERSIONEN_MONATE:-12}"
heute="${PSAGENT_HEUTE:-$(date +%F)}"

[[ "$heute" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] || fehler "Datum ungültig: $heute"
[[ "$tage" =~ ^[1-9][0-9]*$ && "$monate" =~ ^[1-9][0-9]*$ ]] || fehler "Aufbewahrung ungültig"
case "$ziel/" in
    /mnt/user/* | /mnt/user0/*)
        fehler "ZIEL muss auf einer Platte liegen (/mnt/diskN/…), nicht unter /mnt/user: $ziel"
        ;;
    "$quelle"/*) fehler "ZIEL darf nicht in QUELLE liegen, dort könnte der Server es ändern" ;;
esac
[[ -d "$quelle" ]] || fehler "QUELLE fehlt: $quelle"

staende() {
    local d
    for d in "$ziel"/[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]; do
        if [[ -d "$d" ]]; then
            printf '%s\n' "${d##*/}"
        fi
    done
}

# Den Ordner über ZIEL nicht anlegen: Fehlt er, ist die Platte nicht eingehängt (Array gestoppt),
# und die Kopie landete im Arbeitsspeicher.
[[ -d "$(dirname -- "$ziel")" ]] || fehler "Ordner für ZIEL fehlt: $(dirname -- "$ziel") (Array gestartet?)"
[[ -d "$ziel" ]] || mkdir -m 700 -- "$ziel"
chmod 700 "$ziel"

letzter="$(staende | tail -n 1)"
if [[ -n "$letzter" && "$letzter" > "$heute" ]]; then
    fehler "Es gibt schon einen Stand vom $letzter, heute ist laut Uhr $heute. Uhr prüfen."
fi

if [[ "$letzter" == "$heute" ]]; then
    echo "Stand $heute gibt es schon."
else
    rm -rf -- "$ziel/.unfertig"
    # -r ohne -l: symbolische Links überspringt rsync, statt ihnen zu folgen.
    args=(-rt "--chmod=D700,F600")
    if [[ -n "$letzter" ]]; then
        args+=("--link-dest=$ziel/$letzter")
    fi
    status=0
    rsync "${args[@]}" "$quelle/" "$ziel/.unfertig/" || status=$?
    # 24: Datei während des Kopierens verschwunden (der Server räumt gerade auf). Unkritisch.
    if ((status != 0 && status != 24)); then
        fehler "Kopieren nach $ziel ist gescheitert (rsync $status)."
    fi
    mv -- "$ziel/.unfertig" "$ziel/$heute"
    echo "Stand $heute angelegt."
fi

# Aufräumen: die letzten $tage Tage und je Monat der erste Stand der letzten $monate Monate.
ab_tag="$(date -d "$heute -$((tage - 1)) days" +%F)"
ab_monat="$(date -d "${heute:0:7}-01 -$((monate - 1)) months" +%Y-%m)"
neuester="$(staende | tail -n 1)"
declare -A monat_gesehen=()
behalten=0
while IFS= read -r name; do
    monat="${name:0:7}"
    erster_im_monat=0
    if [[ -z "${monat_gesehen[$monat]:-}" ]]; then
        monat_gesehen[$monat]=1
        erster_im_monat=1
    fi
    if [[ ! "$name" < "$ab_tag" || "$name" == "$neuester" ]]; then
        behalten=$((behalten + 1))
        continue
    fi
    if ((erster_im_monat)) && [[ ! "$monat" < "$ab_monat" ]]; then
        behalten=$((behalten + 1))
        continue
    fi
    rm -rf -- "${ziel:?}/$name"
    echo "Gelöscht: $name"
done < <(staende)

wort="Stände"
if ((behalten == 1)); then
    wort="Stand"
fi
echo "Behalten: $behalten $wort. Belegt: $(du -sh "$ziel" | cut -f1)"
