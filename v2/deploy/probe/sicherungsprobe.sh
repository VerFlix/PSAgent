#!/usr/bin/env bash
# Probe für Sicherung und Wiederherstellung (#23, #25) mit künstlichen Daten und
# Wegwerf-Schlüsseln. Läuft in der CI (Job „Sicherung und Wiederherstellung (Probe)“) als root
# auf einem frischen Rechner, auf dem die Images mit dem Tag schon gebaut sind:
#
#     sudo deploy/probe/sicherungsprobe.sh sha-<commit>
#
# Ablauf wie im Betrieb, nur mit sshd auf diesem Rechner statt Unraid:
#   1. Ziel, Schlüssel und /etc/psagent anlegen, PSAgent starten, Daten anlegen
#   2. sicherung.sh; prüfen: alles da, nur Kettenköpfe lesbar
#   3. weitere Daten nur im WAL, dann alles löschen
#   4. wiederherstellen.sh --probe; prüfen: Daten, Datei, Kette, nichts zurückgeschrieben
#   5. noch einmal löschen, wiederherstellen.sh --ernstfall; prüfen: neue Zeitlinie gesichert
#
# Nie auf dem Server ausführen: Das Skript legt /etc/psagent und /var/lib/psagent neu an. Es bricht
# ab, wenn es dort etwas findet, das nicht von einer früheren Probe stammt.
set -euo pipefail

tag="${1:?Image-Tag angeben, z. B. sha-<commit>}"
deploy="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
work=/var/tmp/psagent-sicherungsprobe
target=/srv/psagent-sicherungsprobe
port=2222
compose=(docker compose --project-directory "$deploy" --env-file /etc/psagent/image.env)
manage=("$deploy/manage.sh")

step() { printf '\n=== %s\n' "$*"; }
die() {
    echo "PROBE GESCHEITERT: $*" >&2
    exit 1
}
shell() { "${manage[@]}" shell -c "$1" </dev/null | tail -n 1; }

# --- 0. Nur auf einem frischen Rechner ---

for path in /etc/psagent /var/lib/psagent; do
    if [[ -e "$path" && ! -e "$work/marke" ]]; then
        die "$path gibt es schon und stammt nicht von einer Probe. Nie auf dem Server ausführen."
    fi
done
if [[ -e "$work/marke" ]]; then
    "${compose[@]}" down --remove-orphans >/dev/null 2>&1 || true
    [[ -f "$work/sshd.pid" ]] && kill "$(cat "$work/sshd.pid")" 2>/dev/null || true
    rm -rf /etc/psagent /var/lib/psagent "$target" "$work"
fi
mkdir -p "$work" && touch "$work/marke"

step "1. Ziel per SFTP (sshd auf Port $port, nur SFTP, nur Schlüssel)"
id psagent-sicherung >/dev/null 2>&1 || useradd --system --home-dir "$target" --shell /usr/sbin/nologin psagent-sicherung
usermod -p '*' psagent-sicherung # kein Passwort, aber nicht gesperrt (sshd ohne PAM)
install -d -m 755 "$target"
install -d -o psagent-sicherung -m 700 "$target/psagent"
install -d -m 700 /etc/psagent /etc/psagent/sicherung
ssh-keygen -q -t ed25519 -N '' -C psagent-probe -f /etc/psagent/sicherung/ssh_key
ssh-keygen -q -t ed25519 -N '' -f "$work/host_key"
install -m 644 /etc/psagent/sicherung/ssh_key.pub "$work/authorized_keys"
cat >"$work/sshd_config" <<EOF
Port $port
HostKey $work/host_key
PidFile $work/sshd.pid
AuthorizedKeysFile $work/authorized_keys
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
StrictModes no
Subsystem sftp internal-sftp
ForceCommand internal-sftp
AllowUsers psagent-sicherung
EOF
if [[ ! -x /usr/sbin/sshd ]]; then
    apt-get update -qq && apt-get install -y -qq openssh-server >/dev/null
fi
mkdir -p /run/sshd
/usr/sbin/sshd -f "$work/sshd_config"

step "1. Schlüssel (Wegwerf, nur für die Probe) und Einstellungen"
passphrase="$(openssl rand -hex 16)"
docker run --rm -e PASSPHRASE="$passphrase" -v "$work:/aus" "ghcr.io/verflix/psagent-db:$tag" bash -c '
    set -e
    export GNUPGHOME="$(mktemp -d)"
    gpg --batch --quiet --pinentry-mode loopback --passphrase-fd 3 \
        --quick-gen-key "PSAgent Sicherungsprobe <probe@example.org>" default default never 3<<<"$PASSPHRASE"
    gpg --batch --armor --export > /aus/public.asc
    gpg --batch --pinentry-mode loopback --passphrase-fd 3 --armor --export-secret-keys \
        > /aus/privat.asc 3<<<"$PASSPHRASE"'
install -m 644 "$work/public.asc" /etc/psagent/sicherung/public.asc
chgrp 999 /etc/psagent/sicherung/ssh_key && chmod 640 /etc/psagent/sicherung/ssh_key
chmod 755 /etc/psagent /etc/psagent/sicherung
install -d -m 750 -g 10001 /etc/psagent/certs
install -d -m 700 /var/lib/psagent/postgres
install -d -o 10001 -g 10001 -m 750 /var/lib/psagent/media /var/lib/psagent/kettenkopf
printf 'PSAGENT_IMAGE_TAG=%s\n' "$tag" >/etc/psagent/image.env
host=psagent.probe.ts.net
cat >/etc/psagent/psagent.env <<EOF
POSTGRES_PASSWORD=$(openssl rand -hex 24)
DJANGO_SECRET_KEY=$(openssl rand -hex 32)
DJANGO_ALLOWED_HOSTS=$host
DJANGO_CSRF_TRUSTED_ORIGINS=https://$host
PSAGENT_WEBAUTHN_RP_ID=$host
PSAGENT_WEBAUTHN_ORIGINS=https://$host
PSAGENT_BASE_URL=https://$host
PSAGENT_CLUB_NAME=Beispielverein Sektion Probe
PSAGENT_MAIL_BACKEND=django.core.mail.backends.console.EmailBackend
WALG_SSH_PREFIX=ssh://172.30.0.1$target/psagent
SSH_USERNAME=psagent-sicherung
SSH_PORT=$port
EOF
chmod 600 /etc/psagent/psagent.env

step "1. PSAgent starten und Daten anlegen"
"${compose[@]}" up -d --wait db
"${compose[@]}" run --rm app python manage.py migrate --noinput >/dev/null
"${compose[@]}" up -d --wait app
shell 'from inventory import services
services.create_product({"ei": "PROBE-1", "designation": "Sicherungsprobe"})
doc = services.store_document_file(b"%PDF-1.4 Sicherungsprobe", "probe.pdf", title="GAL Probe")
print(doc.file.name)' >"$work/gal"
gal="$(cat "$work/gal")"
"${manage[@]}" kettenkopf </dev/null || true # ohne Zeitstempeldienst und Mail: meldet Fehler
heads=(/var/lib/psagent/kettenkopf/*.txt)
[[ -f "${heads[0]}" ]] || die "kettenkopf hat keine Datei geschrieben."

step "2. Sicherung"
"$deploy/sicherung.sh"
store="$target/psagent"
ls "$store"/basebackups_005/*_backup_stop_sentinel.json >/dev/null || die "Keine Vollsicherung."
ls "$store"/dateien/dateien-*.tar.zst >/dev/null || die "Kein Datei-Archiv."
cmp "${heads[0]}" "$store/kettenkopf/$(basename "${heads[0]}")" || die "Kettenkopf nicht lesbar abgelegt."
# Alles außer den Kettenköpfen ist für den Schlüssel der Probe verschlüsselt.
docker run --rm -v "$store:/s:ro" "ghcr.io/verflix/psagent-db:$tag" bash -c '
    set -e
    for file in /s/dateien/*.zst /s/wal_005/*.zst /s/basebackups_005/*/tar_partitions/*.zst; do
        gpg --batch --list-packets "$file" 2>&1 | grep -q "pubkey enc packet" \
            || { echo "unverschlüsselt: $file" >&2; exit 1; }
    done' || die "Unverschlüsselte Datei in der Sicherung."

step "3. Daten nur im WAL, dann alles löschen"
shell 'from inventory import services
services.create_product({"ei": "PROBE-2", "designation": "nur im WAL"})'
head="$(shell 'from core import chain; h = chain.head(); print(f"{h.seq}:{h.hash}")')"
segment="$("${compose[@]}" exec -T -u postgres db psql -Atc 'select pg_walfile_name(pg_switch_wal())')"
for ((i = 0; ; i++)); do
    archived="$("${compose[@]}" exec -T -u postgres db psql -Atc 'select last_archived_wal from pg_stat_archiver')"
    [[ "$archived" > "$segment" || "$archived" == "$segment" ]] && break
    ((i < 60)) || die "WAL-Abschnitt $segment nicht archiviert (zuletzt: $archived)."
    sleep 2
done
wipe() {
    "${compose[@]}" down
    find /var/lib/psagent/postgres /var/lib/psagent/media /var/lib/psagent/kettenkopf \
        -mindepth 1 -delete
}
wipe

check_restored() {
    local count
    count="$(shell 'from inventory.models import Component
print(Component.objects.filter(ei__in=["PROBE-1", "PROBE-2"]).count())')"
    [[ "$count" == 2 ]] || die "Teile nach der Wiederherstellung: $count statt 2."
    "${compose[@]}" exec -T app test -f "/var/lib/psagent/media/$gal" || die "GAL-Datei fehlt."
}

step "4. Wiederherstellungsprobe"
wal_before="$(find "$store/wal_005" -type f | wc -l)"
WALG_PGP_KEY_PASSPHRASE="$passphrase" "$deploy/wiederherstellen.sh" --probe "$work/privat.asc" \
    --kopf "$head" || die "wiederherstellen.sh --probe endete mit $?."
check_restored
"${compose[@]}" exec -T -u postgres db psql -Atc 'select pg_switch_wal()' >/dev/null
sleep 5
wal_after="$(find "$store/wal_005" -type f | wc -l)"
[[ "$wal_after" == "$wal_before" ]] || die "Die Probe hat in die Sicherung geschrieben."
output="$("$deploy/sicherung.sh" 2>&1)"
grep -q Probebetrieb <<<"$output" || die "sicherung.sh läuft auf dem Probegerät."

step "5. Ernstfall"
wipe
rm /etc/psagent/sicherung/probe
WALG_PGP_KEY_PASSPHRASE="$passphrase" "$deploy/wiederherstellen.sh" --ernstfall "$work/privat.asc" \
    --kopf "$head" || die "wiederherstellen.sh --ernstfall endete mit $?."
check_restored
ls "$store"/wal_005/00000002.history* >/dev/null || die "Neue Zeitlinie nicht gesichert."
backups="$(find "$store/basebackups_005" -name '*_backup_stop_sentinel.json' | wc -l)"
[[ "$backups" -ge 2 ]] || die "Keine frische Vollsicherung nach dem Ernstfall ($backups)."

step "Probe bestanden"
