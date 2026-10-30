# Runbook: Server einrichten und PSAgent betreiben

Gilt ab #22. **Einrichten lokal mit Felix**, Schritt für Schritt am Gerät. Was hier steht, ist in
der Cloud nur gegen die Dateien in `deploy/` geprüft (`tests/test_deploy.py`), noch nicht auf dem
echten Gerät.

## Überblick

- Server ist ein Mini-PC **Fujitsu Futro S740** (Celeron J4105, 4 GB RAM, M.2-SATA-SSD, TPM 2.0)
  an einem abschließbaren Ort im DAV, mit **Debian 13** ohne Desktop. Die SSD ist mit LUKS
  verschlüsselt und wird beim Start über das TPM entsperrt (Festlegung Felix, 30.10.2026).
- Zugriff **nur über Tailscale** unter `https://psagent.<tailnet>.ts.net`. Kein Port ins
  Internet, keine Portfreigabe am Router.
- HTTPS macht `tailscale serve` auf dem Server, mit Zertifikat von Tailscale (Let's Encrypt).
  `tailscale serve` leitet an die App auf `127.0.0.1:8000` weiter. Einen eigenen Webserver gibt
  es nicht; CSS und JavaScript liefert die App selbst aus (WhiteNoise).
- App und PostgreSQL laufen in Docker (`deploy/compose.yaml`). Die Images baut die CI nach jedem
  Merge auf `v2` für amd64 und arm64 und legt sie unter `ghcr.io/verflix/psagent:sha-<commit>`
  und `ghcr.io/verflix/psagent-db:sha-<commit>` (PostgreSQL mit WAL-G) ab.
- Tägliche Befehle laufen als systemd-Timer (`deploy/systemd/`).
- Sicherung nach Unraid und Wiederherstellung: `docs/runbooks/sicherung.md` (#23, #25).
  Monitoring mit Uptime Kuma auf Unraid: `docs/runbooks/monitoring.md` (#24).
- Ein Raspberry Pi 5 kann als Ersatzgerät dienen (Anhang).

| Ort auf dem Server | Inhalt |
| --- | --- |
| `/opt/psagent` | Klon des Repos, Stand passend zum Image |
| `/etc/psagent/psagent.env` | Geheimnisse und Einstellungen (Vorlage `deploy/psagent.env.example`) |
| `/etc/psagent/image.env` | Laufende Version, schreibt `update.sh` |
| `/etc/psagent/certs/` | Zertifikate der Vertrauensstellen, Graph-Schlüssel |
| `/etc/psagent/sicherung/` | Öffentlicher Sicherungsschlüssel, SSH-Schlüssel für Unraid |
| `/var/lib/psagent/postgres` | Datenbank |
| `/var/lib/psagent/media` | Hochgeladene Dateien (GAL, Scans, signierte PDFs) |
| `/var/lib/psagent/kettenkopf` | Tägliche Kopf-Dateien der Hash-Kette |

## 1. Gerät und Betriebssystem

### 1.1 Gerät prüfen

Das vorinstallierte System eines gebrauchten Geräts bekommt kein Netz; es wird ohnehin ersetzt.

- SSD: im vorinstallierten System mit „Laufwerke“ (gnome-disks) → ⋮ → „SMART-Werte und
  Selbsttests“: neu zugewiesene Sektoren müssen 0 sein. Viele SSDs melden ihre Abnutzung nicht;
  dann überwacht der stündliche Zustand die übrigen Werte (`monitoring.md`).
- Größe: Der Start mit 32 GB geht (Festlegung Felix). Später auf 128–256 GB tauschen: nur
  **M.2 SATA** (2242 oder 2280), kein NVMe. Der Tausch heißt neu installieren und aus der Sicherung
  wiederherstellen (`sicherung.md`, Ernstfall) und ist damit zugleich eine echte Probe.
- TPM: `ls /dev/tpm0` und `cat /sys/class/tpm/tpm0/tpm_version_major` (muss `2` sein).

### 1.2 BIOS (F2 beim Einschalten)

- **TPM löschen** („Clear“), damit nichts vom Vorbesitzer bleibt, danach TPM bzw. PTT
  **einschalten**.
- **Secure Boot ein**. Debian startet signiert über `shim`; das TPM-Entsperren hängt daran.
- **Nach Stromausfall einschalten** („Power on after AC loss“ oder ähnlich).
- Nur UEFI; Netzwerk-Boot (PXE) aus; USB-Boot nur für die Installation, danach aus.
- **Administrator Password** setzen (Tresor); es schützt die BIOS-Einstellungen. **Kein User
  Password** und kein Passwort beim Start („Password on boot“ aus): Sonst bleibt der Server nach
  jedem Stromausfall und Update-Neustart stehen.
- **Password Severity: Strong.** Das Administrator Password lässt sich dann nicht mehr über die
  Brücke „Password skip“ auf der Hauptplatine löschen, nur noch über den Fujitsu-Service; Fujitsu
  empfiehlt diese Stufe. „Standard“ ließe jeden, der das Gehäuse öffnet, das Passwort löschen,
  USB-Start erlauben und so womöglich an den TPM-Schlüssel kommen. „Stringent“ schließt auch den
  Service aus; geht das Passwort dann verloren, lässt sich das BIOS nie mehr ändern.
- Eine WLAN-Karte, falls vorhanden, abschalten oder ausbauen.

### 1.3 Debian 13 installieren

1. Auf dem eigenen Rechner das Abbild „netinst“ (amd64) von <https://www.debian.org/distrib/>
   laden, gegen `SHA512SUMS` samt Signatur prüfen und auf einen USB-Stick schreiben.
2. **Felix, nicht Claude:** eine lange LUKS-Passphrase erzeugen und in den Tresor legen. Sie ist
   der Rückfall, wenn das TPM einmal nicht entsperrt.
3. Installation:
   - Hostname `psagent`; kein Root-Passwort, ein eigener Benutzer mit `sudo`.
   - Partitionierung: **„Geführt – gesamte Platte mit verschlüsseltem LVM“**, alle Dateien auf
     eine Partition, die Passphrase aus Schritt 2.
   - Softwareauswahl: nur **„SSH-Server“** und **„Standard-Systemwerkzeuge“**, kein Desktop.
4. Nach dem ersten Start (fragt noch nach der Passphrase) den eigenen öffentlichen SSH-Schlüssel
   hinterlegen (`ssh-copy-id`, solange noch Passwörter gehen; Schritt 3 schaltet sie ab).

### 1.4 Entsperren über das TPM

Debians Standard-Initramfs (`initramfs-tools`) kann TPM-Token von `systemd-cryptenroll` nicht
nutzen; Clevis hängt sich dort ein. Gebunden wird an **PCR 7** (Secure-Boot-Zustand): Das übersteht
Kernel-Updates, sodass nächtliche Neustarts ohne Passphrase gehen.

```bash
sudo apt update && sudo apt full-upgrade -y
sudo apt install -y tpm2-tools clevis clevis-luks clevis-tpm2 clevis-initramfs
mokutil --sb-state                     # muss „SecureBoot enabled“ zeigen
lsblk -f                               # Partition mit crypto_LUKS, meist /dev/sda3
sudo clevis luks bind -d /dev/sda3 tpm2 '{"pcr_bank":"sha256","pcr_ids":"7"}'
sudo update-initramfs -u -k all
sudo clevis luks list -d /dev/sda3     # zeigt die Bindung (Slot 1)
sudo reboot                            # startet jetzt ohne Passphrase
```

**Mit Bildschirm und Tastatur am Gerät erproben;** die Passphrase bleibt immer als Rückfall.

- Fragt das Gerät nach einem BIOS-Update oder einer Änderung an Secure Boot wieder nach der
  Passphrase: einmal eingeben, dann `sudo clevis luks regen -d /dev/sda3 -s 1`.
- **Schutzwirkung:** Geschützt sind eine ausgebaute SSD und ein ausgemustertes Gerät. Bei
  Diebstahl des ganzen Geräts schützt das nur eingeschränkt: Wer ein anderes signiertes System
  startet, kommt bei reinem PCR 7 womöglich an den Schlüssel. Administrator Password mit
  Severity „Strong“ und abgeschalteter USB-Boot erschweren das; das Siegel am Schrank zeigt, ob
  jemand am Gerät war. Im Betrieb ist die SSD offen; dagegen helfen Tailscale, SSH nur mit
  Schlüssel und die Anmeldung der App.

### 1.5 Zeitzone, Updates, SMART

```bash
sudo timedatectl set-timezone Europe/Berlin   # die Timer laufen in Ortszeit
sudo apt install -y unattended-upgrades smartmontools
sudo smartctl -a /dev/sda                     # Werte für die Akte
```

Sicherheitsupdates spielt `unattended-upgrades` automatisch ein, Debian und Docker. Braucht ein
Update einen Neustart (Kernel), startet der Server um 04:30 neu, nach Sicherung (02:00) und
Löschfristen (03:00); das TPM entsperrt dabei ohne Eingabe.

```bash
sudo tee /etc/apt/apt.conf.d/52psagent >/dev/null <<'EOF'
// PSAgent (#22): auch Docker automatisch, Neustart bei Bedarf nachts, alte Kernel weg (32-GB-SSD).
Unattended-Upgrade::Origins-Pattern { "origin=Docker,label=Docker CE"; };
Unattended-Upgrade::Automatic-Reboot "true";
Unattended-Upgrade::Automatic-Reboot-Time "04:30";
Unattended-Upgrade::Remove-Unused-Kernel-Packages "true";
Unattended-Upgrade::Remove-Unused-Dependencies "true";
EOF
# Updates um 04:00 statt irgendwann am Morgen; ein Docker-Update startet die Container neu.
sudo mkdir -p /etc/systemd/system/apt-daily-upgrade.timer.d
sudo tee /etc/systemd/system/apt-daily-upgrade.timer.d/psagent.conf >/dev/null <<'EOF'
[Timer]
OnCalendar=
OnCalendar=*-*-* 04:00
RandomizedDelaySec=0
EOF
sudo systemctl daemon-reload
sudo unattended-upgrade --dry-run --debug 2>&1 | grep -i "allowed origins"
```

Startet der Server nach einem Neustart nicht (z. B. Passphrase verlangt), meldet das Monitoring
„PSAgent erreichbar“ nach wenigen Minuten (`monitoring.md`).

## 2. Tailscale

1. Tailscale nach der Anleitung für Debian installieren
   (<https://tailscale.com/download/linux>) und anmelden:

   ```bash
   sudo tailscale up --hostname=psagent
   sudo tailscale set --auto-update
   ```

2. In der Tailscale-Verwaltung:
   - beim Gerät `psagent` **„Disable key expiry“**, sonst fällt der Server nach 180 Tagen aus dem
     Tailnet;
   - unter DNS **MagicDNS** und **HTTPS Certificates** einschalten. Hinweis: Der Name
     `psagent.<tailnet>.ts.net` steht danach in den öffentlichen Certificate-Transparency-Logs;
     Inhalte der App nicht;
   - in der Richtlinie (Access controls) Funnel abschalten: einen Eintrag mit `"attr":
     ["funnel"]` unter `nodeAttrs` entfernen, falls vorhanden.
3. Der Name `psagent.<tailnet>.ts.net` darf sich danach **nicht mehr ändern**: Passkeys,
   QR-Etiketten und Links in Mails hängen daran.

## 3. Firewall und SSH

Nur HTTPS und SSH aus dem Tailnet, SSH nur mit Schlüssel. **Erst prüfen, dass SSH über
Tailscale geht** (`ssh <benutzer>@psagent.<tailnet>.ts.net`), dann sperren; Tastatur und
Bildschirm am Server bereithalten.

```bash
sudo tee /etc/ssh/sshd_config.d/psagent.conf >/dev/null <<'EOF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
EOF
sudo systemctl reload ssh

sudo apt install -y ufw
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow in on tailscale0 to any port 22 proto tcp
sudo ufw allow in on tailscale0 to any port 443 proto tcp
sudo ufw enable
sudo ufw status verbose
```

Die App lauscht nur auf `127.0.0.1:8000`, die Datenbank hat keinen Port nach außen
(`deploy/compose.yaml`). Docker umgeht ufw nur für veröffentlichte Ports; an `127.0.0.1`
gebundene erreicht aus dem Netz niemand.

## 4. Docker

Docker Engine mit Compose-Plugin nach der offiziellen Anleitung für Debian installieren
(<https://docs.docker.com/engine/install/debian/>, arm64). Danach:

```bash
docker compose version
```

## 5. Ordner, Einstellungen, Zertifikate

```bash
sudo install -d -m 700 /var/lib/psagent/postgres
sudo install -d -o 10001 -g 10001 -m 750 /var/lib/psagent/media /var/lib/psagent/kettenkopf
sudo install -d -m 755 /etc/psagent
sudo install -d -g 10001 -m 750 /etc/psagent/certs
```

Die App läuft im Container als Benutzer 10001; PostgreSQL richtet seinen Ordner selbst ein.

1. Repo klonen (öffentlich, ohne Zugangsdaten):

   ```bash
   sudo git clone https://github.com/VerFlix/PSAgent.git /opt/psagent
   ```

2. Einstellungen aus der Vorlage anlegen und ausfüllen; die Datei bleibt nur für root lesbar:

   ```bash
   sudo install -m 600 /opt/psagent/v2/deploy/psagent.env.example /etc/psagent/psagent.env
   sudo nano /etc/psagent/psagent.env
   ```

   Passwort und Schlüssel mit `openssl rand -base64 32` bzw. `48` erzeugen (Felix, nicht
   Claude) und zusätzlich im Tresor ablegen. Ohne `POSTGRES_PASSWORD` lässt sich die Datenbank
   nicht wiederherstellen (#25).
3. Zertifikate nach `/etc/psagent/certs/` (Pfade wie in der Vorlage): Zeitstempeldienst
   (`docs/runbooks/kettenkopf.md`), Yubico (`docs/runbooks/zertifikate.md`), Graph-Schlüssel
   (`docs/runbooks/mail-m365.md`). Der Graph-Schlüssel ist geheim:

   ```bash
   sudo chown root:10001 /etc/psagent/certs/graph.pem
   sudo chmod 640 /etc/psagent/certs/graph.pem
   ```

4. Sicherung einrichten, **bevor** die Datenbank zum ersten Mal startet:
   `docs/runbooks/sicherung.md`, Abschnitte 1–3 (ohne den Timer, der kommt in Schritt 7).

## 6. Image holen und starten

1. Version wählen: GitHub → Repository → Packages → `psagent`, oder in GitHub Actions der Lauf
   „v2-ci“ des gewünschten Merge-Commits mit grünem Job „Image veröffentlichen (GHCR)“. Das
   Tag heißt `sha-<voller Commit-Hash>`.
2. Beim **ersten** Image einmalig die Sichtbarkeit des Pakets prüfen (Package settings). Ist es
   privat, auf **Public** stellen: Das Image enthält nur den ohnehin öffentlichen Code, keine
   Geheimnisse und keine Daten.
3. Klon auf denselben Stand bringen und starten:

   ```bash
   cd /opt/psagent
   sudo git fetch origin && sudo git checkout <commit>
   sudo v2/deploy/update.sh sha-<commit>
   ```

   `update.sh` holt das Image, startet die Datenbank, migriert, startet die App und wartet, bis
   `/healthz` antwortet. Die vorige Version steht danach in `/etc/psagent/image.env.vorher`.
   Ältere Images löscht es; die laufende und die vorige Version bleiben für ein Zurück.
4. HTTPS im Tailnet einschalten (bleibt nach Neustarts bestehen):

   ```bash
   sudo tailscale serve --bg --https=443 http://127.0.0.1:8000
   tailscale serve status
   ```

   **Nie `tailscale funnel`.** Das stellte die App ins Internet.
5. Von einem anderen Gerät im Tailnet: `https://psagent.<tailnet>.ts.net/healthz` muss
   `{"status": "ok", …}` zeigen.
6. Erstes Admin-Konto und Passkey (README, „Anmeldung“):

   ```bash
   sudo /opt/psagent/v2/deploy/manage.sh createsuperuser
   sudo /opt/psagent/v2/deploy/manage.sh passkey_einrichtung <benutzername>
   ```

7. **Client-Adresse prüfen** (Sperre nach Fehlversuchen): von einem Gerät im Tailnet einmal
   mit falschem Passwort anmelden, dann

   ```bash
   sudo /opt/psagent/v2/deploy/manage.sh axes_list_attempts
   ```

   Vorn muss die Tailscale-Adresse des Geräts stehen (`100.x.y.z`), nicht `172.30.0.1`. Steht
   dort das Gateway, kommt `X-Forwarded-For` nicht an. Dann bitte melden und nicht weiter
   einrichten: Sonst sperren fünf Fehlversuche eines Geräts denselben Benutzernamen für alle.
   Danach den Fehlversuch löschen: `sudo /opt/psagent/v2/deploy/manage.sh axes_reset`.

## 7. Timer

| Timer | Befehl | Zeit |
| --- | --- | --- |
| `psagent-kettenkopf.timer` | `kettenkopf` | täglich 23:50 |
| `psagent-kette-pruefen.timer` | `kette_pruefen` | täglich 00:10 |
| `psagent-sicherung.timer` | `sicherung.sh` (Sicherung nach Unraid) | täglich 02:00 |
| `psagent-loeschfristen.timer` | `loeschfristen` | täglich 03:00 |
| `psagent-erinnerungen.timer` | `erinnerungen` | täglich 09:00 |
| `psagent-notfallmappe.timer` | `notfallmappe` | montags 06:00 |
| `psagent-zustand.timer` | `zustand.sh` (App, Datenbank, WAL-Archiv, Platz) | stündlich, :17 |

Verpasste Läufe (Server aus) holt systemd nach dem Start nach. Vor dem Einschalten jeden Befehl
einmal von Hand laufen lassen, die Löschfristen zuerst als Probelauf
(`docs/runbooks/loeschfristen.md`):

```bash
sudo /opt/psagent/v2/deploy/manage.sh loeschfristen --probelauf
sudo /opt/psagent/v2/deploy/manage.sh kettenkopf
sudo /opt/psagent/v2/deploy/manage.sh kette_pruefen

sudo cp /opt/psagent/v2/deploy/systemd/psagent-* /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now psagent-kettenkopf.timer psagent-kette-pruefen.timer \
    psagent-sicherung.timer psagent-loeschfristen.timer psagent-erinnerungen.timer \
    psagent-notfallmappe.timer psagent-zustand.timer
systemctl list-timers 'psagent-*'
```

Fehler stehen im Journal: `journalctl -u 'psagent-befehl@*'`, `journalctl -u psagent-sicherung`
und `journalctl -u psagent-zustand`. Jeder Timer meldet nach dem Lauf einen Herzschlag an Uptime
Kuma, ein Fehler sofort (`deploy/melden.sh`, `docs/runbooks/monitoring.md`).

## 8. Neue Version einspielen

1. Nach dem Merge auf `v2` den grünen Lauf von „v2-ci“ abwarten (Job „Image veröffentlichen“).
2. Vorher sichern: `sudo /opt/psagent/v2/deploy/sicherung.sh`.
3. Wie in Schritt 6.3: Klon auf den Commit, dann `sudo v2/deploy/update.sh sha-<commit>`.
4. Haben sich Dateien unter `deploy/systemd/` geändert, sie erneut kopieren und
   `sudo systemctl daemon-reload`.

**Zurück auf die vorige Version:** `cat /etc/psagent/image.env.vorher`, dann Klon und
`update.sh` mit diesem Tag. Das geht nur, wenn die neue Version keine Migration mitgebracht hat:
Migrationen laufen nur vorwärts. Zurück über eine Migration hinweg heißt: Stand von vor dem Update
wiederherstellen. Das kann `wiederherstellen.sh` noch nicht (es holt immer den neuesten Stand);
dann bitte Felix fragen.

## 9. Prüfliste nach der Einrichtung

- [ ] `https://psagent.<tailnet>.ts.net/healthz` aus dem Tailnet erreichbar, aus dem LAN ohne
      Tailscale **nicht** (`http://<LAN-IP-des-Servers>:8000` und `:443` scheitern)
- [ ] `tailscale serve status` zeigt nur `https://psagent.<tailnet>.ts.net` → `127.0.0.1:8000`,
      kein Funnel
- [ ] `sudo ufw status` zeigt nur 22 und 443 auf `tailscale0`
- [ ] SSH mit Passwort wird abgewiesen
- [ ] Anmeldung mit Passkey funktioniert
- [ ] Fehlversuch zeigt die Tailscale-Adresse des Geräts (Schritt 6.7)
- [ ] `systemctl list-timers 'psagent-*'` zeigt sieben Timer
- [ ] Monitoring abgenommen (`monitoring.md`, Abschnitt 4)
- [ ] Erste Sicherung vollständig (`sicherung.md`, Abschnitt 3), `failed_count` steigt nicht
- [ ] Nach `sudo reboot` startet der Server **ohne Passphrase** (TPM), und App, Datenbank und
      `tailscale serve` laufen wieder
- [ ] `sudo clevis luks list -d /dev/sda3` zeigt die TPM-Bindung; die Passphrase liegt im Tresor
- [ ] `systemctl list-timers apt-daily-upgrade.timer` zeigt 04:00; Docker steht in den
      „Allowed origins“ von `unattended-upgrade --dry-run --debug`

## Anhang: Raspberry Pi 5 als Ersatzgerät

Die Images gibt es auch für arm64. Statt Schritt 1:

1. Raspberry Pi OS Lite (64-bit) mit dem Raspberry Pi Imager **auf eine NVMe-SSD** schreiben,
   nicht auf die microSD-Karte. Im Imager: Hostname (z. B. `psagent-probe`), eigener Benutzer, SSH
   mit öffentlichem Schlüssel.
2. Bootreihenfolge auf NVMe stellen, falls er nicht von selbst davon startet:
   `sudo raspi-config` → Advanced Options → Boot Order.
3. Zeitzone und Updates wie in 1.5; die Docker-Zeile gilt genauso. Ohne TPM bleibt die SSD des Pi
   unverschlüsselt (Konzept, Stufe 1).

Ab Schritt 2 gilt alles wie oben.
