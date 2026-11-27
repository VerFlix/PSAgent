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
# Vor dem Aufräumen prüft es den neuen Stand. Ist er leer, ist die neueste Datei älter als
# 26 Stunden oder ist er auf weniger als die Hälfte des letzten unauffälligen Stands geschrumpft,
# löscht es nichts und meldet (Exit 1). Sonst hätte ein gehackter Server, der seine Sicherung
# löscht oder ersetzt, nach 30 Tagen auch die täglichen Stände von vor dem Angriff beseitigt. Den
# letzten unauffälligen Stand merkt sich ZIEL/.referenz („<Datum> <Byte>“). Ist ein kleinerer
# Stand erklärbar, nimmt ihn ein Lauf von Hand mit PSAGENT_VERSIONEN_STAND_ANNEHMEN=1 an.
#
# Meldungen gehen an Unraid (Glocke) und, wenn PSAGENT_VERSIONEN_PUSH gesetzt ist, an die
# Push-Adresse eines Monitors in Uptime Kuma (status=up bzw. down, nie Inhalte der Sicherung).
#
# Einstellbar: PSAGENT_VERSIONEN_TAGE (30) und PSAGENT_VERSIONEN_MONATE (12); bei einem Vorfall
# PSAGENT_VERSIONEN_TAGE=3650, dann löscht es nichts (`vorfall-kette.md`).
# PSAGENT_VERSIONEN_FRISCH_STUNDEN (26) und PSAGENT_VERSIONEN_MIN_PROZENT (50) für die Prüfung.
# Für Tests: PSAGENT_HEUTE (Datum) und PSAGENT_UNRAID_NOTIFY (Meldeskript von Unraid).
set -euo pipefail
shopt -s nullglob

notify="${PSAGENT_UNRAID_NOTIFY:-/usr/local/emhttp/webGui/scripts/notify}"
push="${PSAGENT_VERSIONEN_PUSH:-}"

unraid_melden() {
    if [[ -x "$notify" ]]; then
        "$notify" -e "PSAgent-Sicherung" -s "$1" -d "$2" -i alert || true
    fi
}

kuma_melden() {
    if [[ -n "$push" ]]; then
        curl -fsS --max-time 10 --retry 2 -o /dev/null -G \
            --data-urlencode "status=$1" --data-urlencode "msg=$2" "$push" \
            || echo "Meldung an Uptime Kuma fehlgeschlagen." >&2
    fi
}

fehler() {
    echo "FEHLER: $1" >&2
    unraid_melden "Versionsstand fehlgeschlagen" "$1"
    kuma_melden down "Fehler: $1"
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
frisch="${PSAGENT_VERSIONEN_FRISCH_STUNDEN:-26}"
min_prozent="${PSAGENT_VERSIONEN_MIN_PROZENT:-50}"
[[ "$frisch" =~ ^[1-9][0-9]*$ && "$min_prozent" =~ ^[0-9]+$ ]] || fehler "Prüfwerte ungültig"
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

# Prüfung vor dem Aufräumen (siehe oben).
stand="$ziel/$heute"
referenz="$ziel/.referenz"
dateien="$(find "$stand" -type f | wc -l)"
bytes="$(find "$stand" -type f -printf '%s\n' | awk '{ s += $1 } END { print s + 0 }')"
befund=()
if ((dateien == 0)); then
    befund+=("Die Sicherung ist leer.")
else
    neueste="$(find "$stand" -type f -printf '%T@\n' | sort -n | tail -n 1)"
    neueste="${neueste%.*}"
    if (($(date +%s) - neueste > frisch * 3600)); then
        einheit="Stunden"
        if ((frisch == 1)); then
            einheit="Stunde"
        fi
        befund+=("Die neueste Datei ist älter als $frisch $einheit (vom $(date -d "@$neueste" '+%d.%m.%Y %H:%M')).")
    fi
fi
referenz_tag=""
referenz_bytes=0
if [[ -f "$referenz" ]]; then
    read -r referenz_tag referenz_bytes <"$referenz" || true
fi
[[ "$referenz_bytes" =~ ^[0-9]+$ ]] || referenz_bytes=0
if ((referenz_bytes > 0 && bytes * 100 < referenz_bytes * min_prozent)); then
    if [[ "${PSAGENT_VERSIONEN_STAND_ANNEHMEN:-}" == 1 ]]; then
        echo "Kleinerer Stand angenommen: $bytes Byte statt $referenz_bytes Byte vom $referenz_tag."
    else
        befund+=("Die Sicherung ist auf $((bytes * 100 / referenz_bytes)) % des Stands vom $referenz_tag geschrumpft.")
    fi
fi
if ((${#befund[@]})); then
    text="${befund[*]} Alte Stände werden nicht gelöscht; erst prüfen (sicherung.md, Abschnitt 2)."
    echo "WARNUNG: $text" >&2
    unraid_melden "Sicherung auf Unraid auffällig" "$text"
    kuma_melden down "$text"
    echo "Nichts gelöscht. Belegt: $(du -sh "$ziel" | cut -f1)"
    exit 1
fi
printf '%s %s\n' "$heute" "$bytes" >"$referenz.neu"
chmod 600 "$referenz.neu"
mv -- "$referenz.neu" "$referenz"

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
kuma_melden up "ok: $behalten $wort, $dateien Dateien"
