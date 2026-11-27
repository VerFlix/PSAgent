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
# Vor dem Aufräumen prüft es den neuen Stand. Auffällig ist er, wenn er leer ist, eine Datei von
# gestern verändert wurde, ein Kettenkopf fehlt, Zeitstempel in der Zukunft liegen, die neueste
# Datei älter als 26 Stunden ist oder er auf weniger als die Hälfte des letzten unauffälligen
# Stands (ZIEL/.referenz) geschrumpft ist. Dann löscht es nichts und meldet (Exit 1). Sonst hätte
# ein gehackter Server, der seine Sicherung löscht oder ersetzt, nach 30 Tagen auch die täglichen
# Stände von vor dem Angriff beseitigt. Befunde am Inhalt bleiben offen (ZIEL/.befund), bis ein
# Lauf von Hand mit PSAGENT_VERSIONEN_STAND_ANNEHMEN=1 sie bestätigt.
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

# Dateinamen kommen vom Server: In Meldungen nur Buchstaben, Ziffern und ._/-, höchstens 120 Zeichen.
sauber() {
    printf '%s' "$1" | LC_ALL=C tr -c 'A-Za-z0-9._/-' '_' | cut -c1-120
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

# Prüfung vor dem Aufräumen (siehe oben). Zwei Arten von Befunden:
# - „veraltet“ (Server aus, Sicherung gestört): erledigt sich mit der nächsten Sicherung.
# - Befunde am Inhalt (leer, verändert, Kettenkopf fehlt, Zeit in der Zukunft, geschrumpft): Sie
#   bleiben in ZIEL/.befund offen, bis jemand sie mit PSAGENT_VERSIONEN_STAND_ANNEHMEN=1 bestätigt.
# Größe und Zeitstempel kann ein gehackter Server fälschen; dass eine Datei von gestern heute anders
# ist oder ein Kettenkopf fehlt, sieht Unraid aber ohne Schlüssel. Datei-Archive, Kettenköpfe und
# Objekte von WAL-G schreibt der Server nur einmal; sie ändern sich nie an Ort und Stelle.
stand="$ziel/$heute"
referenz="$ziel/.referenz"
offen="$ziel/.befund"
annehmen="${PSAGENT_VERSIONEN_STAND_ANNEHMEN:-}"
jetzt="$(date +%s)"
vorher=""
while IFS= read -r name; do
    if [[ "$name" < "$heute" ]]; then
        vorher="$name"
    fi
done < <(staende)
dateien="$(find "$stand" -type f | wc -l)"
bytes="$(find "$stand" -type f -printf '%s\n' | awk '{ s += $1 } END { print s + 0 }')"
veraltet=()
inhalt=()
if ((dateien == 0)); then
    inhalt+=("Die Sicherung ist leer.")
else
    neueste="$(find "$stand" -type f -printf '%T@\n' | sort -n | tail -n 1)"
    neueste="${neueste%.*}"
    if ((neueste > jetzt + 3600)); then
        inhalt+=("Dateien haben einen Zeitstempel in der Zukunft ($(date -d "@$neueste" '+%d.%m.%Y %H:%M')).")
    elif ((jetzt - neueste > frisch * 3600)); then
        einheit="Stunden"
        if ((frisch == 1)); then
            einheit="Stunde"
        fi
        veraltet+=("Die neueste Datei ist älter als $frisch $einheit (vom $(date -d "@$neueste" '+%d.%m.%Y %H:%M')).")
    fi
fi
if [[ -n "$vorher" ]]; then
    # Jede Datei von gestern muss heute dieselbe Datei sein (Hardlink, -ef) oder fehlen. Ist sie
    # eine andere Datei oder etwas anderes als eine Datei, wurde sie verändert. Kettenköpfe dürfen
    # auch nicht fehlen.
    geaendert=0
    fehlend=0
    beispiel=""
    while IFS= read -r -d '' pfad; do
        jetzt_pfad="$stand/$pfad"
        if [[ -f "$jetzt_pfad" && ! -L "$jetzt_pfad" ]]; then
            if [[ ! "$jetzt_pfad" -ef "$ziel/$vorher/$pfad" ]]; then
                geaendert=$((geaendert + 1))
                beispiel="${beispiel:-$pfad}"
            fi
        elif [[ -e "$jetzt_pfad" || -L "$jetzt_pfad" ]]; then
            geaendert=$((geaendert + 1))
            beispiel="${beispiel:-$pfad}"
        elif [[ "/$pfad" == */kettenkopf/* ]]; then
            fehlend=$((fehlend + 1))
        fi
    done < <(find "$ziel/$vorher" -type f -printf '%P\0')
    if ((geaendert > 0)); then
        wort="Dateien"
        if ((geaendert == 1)); then
            wort="Datei"
        fi
        inhalt+=("Seit dem Stand vom $vorher wurde $geaendert $wort verändert, z. B. $(sauber "$beispiel"). Das macht der Server nie (Ausnahme: ein wiederholter Upload desselben WAL-Abschnitts). Gehen Hardlinks nicht, liegt ZIEL nicht auf einer Platte.")
    fi
    if ((fehlend > 0)); then
        inhalt+=("$fehlend Kettenkopf-Datei(en) vom $vorher fehlen. Kettenköpfe löscht der Server nie.")
    fi
fi
referenz_tag=""
referenz_bytes=0
if [[ -f "$referenz" ]]; then
    read -r referenz_tag referenz_bytes <"$referenz" || true
fi
[[ "$referenz_bytes" =~ ^[0-9]+$ ]] || referenz_bytes=0
if ((referenz_bytes > 0 && bytes * 100 < referenz_bytes * min_prozent)); then
    inhalt+=("Die Sicherung ist auf $((bytes * 100 / referenz_bytes)) % des Stands vom $referenz_tag geschrumpft.")
fi

befund=()
if [[ "$annehmen" == 1 ]]; then
    for zeile in "${inhalt[@]}"; do
        echo "Angenommen: $zeile"
    done
    rm -f -- "$offen"
    inhalt=()
elif [[ -f "$offen" ]]; then
    befund+=("Offener Befund vom $(cat -- "$offen")")
fi
if ((${#inhalt[@]})) && [[ ! -f "$offen" ]]; then
    printf '%s: %s\n' "$heute" "${inhalt[*]}" >"$offen"
    chmod 600 "$offen"
fi
befund+=("${inhalt[@]}" "${veraltet[@]}")
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
