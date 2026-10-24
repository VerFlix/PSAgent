# Runbook: Pi einrichten und PSAgent betreiben

Gilt ab #22. **Einrichten lokal mit Felix**, Schritt für Schritt auf dem Pi. Was hier steht, ist
in der Cloud nur gegen die Dateien in `deploy/` geprüft (`tests/test_deploy.py`), noch nicht auf
einem echten Pi.

## Überblick

- Raspberry Pi 5 (4 GB) mit NVMe-SSD an einem abschließbaren Ort im DAV.
- Zugriff **nur über Tailscale** unter `https://psagent.<tailnet>.ts.net`. Kein Port ins
  Internet, keine Portfreigabe am Router.
- HTTPS macht `tailscale serve` auf dem Pi, mit Zertifikat von Tailscale (Let's Encrypt).
  `tailscale serve` leitet an die App auf `127.0.0.1:8000` weiter. Einen eigenen Webserver gibt
  es nicht; CSS und JavaScript liefert die App selbst aus (WhiteNoise).
- App und PostgreSQL laufen in Docker (`deploy/compose.yaml`). Das Image baut die CI nach jedem
  Merge auf `v2` und legt es unter `ghcr.io/verflix/psagent:sha-<commit>` ab.
- Tägliche Befehle laufen als systemd-Timer (`deploy/systemd/`).
- Backups nach Unraid und Monitoring folgen mit #23, #25 und #24.

| Ort auf dem Pi | Inhalt |
| --- | --- |
| `/opt/psagent` | Klon des Repos, Stand passend zum Image |
| `/etc/psagent/psagent.env` | Geheimnisse und Einstellungen (Vorlage `deploy/psagent.env.example`) |
| `/etc/psagent/image.env` | Laufende Version, schreibt `update.sh` |
| `/etc/psagent/certs/` | Zertifikate der Vertrauensstellen, Graph-Schlüssel |
| `/var/lib/psagent/postgres` | Datenbank |
| `/var/lib/psagent/media` | Hochgeladene Dateien (GAL, Scans, signierte PDFs) |
| `/var/lib/psagent/kettenkopf` | Tägliche Kopf-Dateien der Hash-Kette |

## 1. Betriebssystem

1. Raspberry Pi OS Lite (64-bit) mit dem Raspberry Pi Imager **auf die NVMe-SSD** schreiben,
   nicht auf die microSD-Karte. Im Imager: Hostname `psagent`, eigener Benutzer, SSH mit
   öffentlichem Schlüssel (kein Passwort).
2. Vom Pi aus die Bootreihenfolge auf NVMe stellen, falls er nicht von selbst davon startet:
   `sudo raspi-config` → Advanced Options → Boot Order.
3. Zeitzone (die Timer laufen in Ortszeit):

   ```bash
   sudo timedatectl set-timezone Europe/Berlin
   sudo apt update && sudo apt full-upgrade -y
   ```

4. Sicherheitsupdates automatisch:

   ```bash
   sudo apt install -y unattended-upgrades
   sudo dpkg-reconfigure -plow unattended-upgrades
   ```

## 2. Tailscale

1. Tailscale nach der Anleitung für Raspberry Pi OS installieren
   (<https://tailscale.com/download/linux>) und anmelden:

   ```bash
   sudo tailscale up --hostname=psagent
   sudo tailscale set --auto-update
   ```

2. In der Tailscale-Verwaltung:
   - beim Gerät `psagent` **„Disable key expiry“**, sonst fällt der Pi nach 180 Tagen aus dem
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
Bildschirm am Pi bereithalten.

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
| `psagent-loeschfristen.timer` | `loeschfristen` | täglich 03:00 |
| `psagent-erinnerungen.timer` | `erinnerungen` | täglich 09:00 |
| `psagent-notfallmappe.timer` | `notfallmappe` | montags 06:00 |

Verpasste Läufe (Pi aus) holt systemd nach dem Start nach. Vor dem Einschalten jeden Befehl
einmal von Hand laufen lassen, die Löschfristen zuerst als Probelauf
(`docs/runbooks/loeschfristen.md`):

```bash
sudo /opt/psagent/v2/deploy/manage.sh loeschfristen --probelauf
sudo /opt/psagent/v2/deploy/manage.sh kettenkopf
sudo /opt/psagent/v2/deploy/manage.sh kette_pruefen

sudo cp /opt/psagent/v2/deploy/systemd/psagent-* /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now psagent-kettenkopf.timer psagent-kette-pruefen.timer \
    psagent-loeschfristen.timer psagent-erinnerungen.timer psagent-notfallmappe.timer
systemctl list-timers 'psagent-*'
```

Fehler stehen im Journal: `journalctl -u 'psagent-befehl@*'`. Ein Lauf mit Rückgabewert
ungleich 0 geht später ans Monitoring (#24).

## 8. Neue Version einspielen

1. Nach dem Merge auf `v2` den grünen Lauf von „v2-ci“ abwarten (Job „Image veröffentlichen“).
2. Vorher ein Backup ziehen (#23, sobald eingerichtet).
3. Wie in Schritt 6.3: Klon auf den Commit, dann `sudo v2/deploy/update.sh sha-<commit>`.
4. Haben sich Dateien unter `deploy/systemd/` geändert, sie erneut kopieren und
   `sudo systemctl daemon-reload`.

**Zurück auf die vorige Version:** `cat /etc/psagent/image.env.vorher`, dann Klon und
`update.sh` mit diesem Tag. Das geht nur, wenn die neue Version keine Migration mitgebracht hat:
Migrationen laufen nur vorwärts. Sonst das Backup von vor dem Update einspielen (#25).

## 9. Prüfliste nach der Einrichtung

- [ ] `https://psagent.<tailnet>.ts.net/healthz` aus dem Tailnet erreichbar, aus dem LAN ohne
      Tailscale **nicht** (`http://<LAN-IP-des-Pi>:8000` und `:443` scheitern)
- [ ] `tailscale serve status` zeigt nur `https://psagent.<tailnet>.ts.net` → `127.0.0.1:8000`,
      kein Funnel
- [ ] `sudo ufw status` zeigt nur 22 und 443 auf `tailscale0`
- [ ] SSH mit Passwort wird abgewiesen
- [ ] Anmeldung mit Passkey funktioniert
- [ ] Fehlversuch zeigt die Tailscale-Adresse des Geräts (Schritt 6.7)
- [ ] `systemctl list-timers 'psagent-*'` zeigt fünf Timer
- [ ] Nach `sudo reboot` laufen App, Datenbank und `tailscale serve` wieder
