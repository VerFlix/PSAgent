# Runbook: Sicherung nach Unraid und Wiederherstellung

Gilt ab #23 und #25. **Einrichten und proben lokal mit Felix.** In der Cloud ist der ganze Ablauf
mit künstlichen Daten und Wegwerf-Schlüsseln geprüft: Der CI-Job „Sicherung und Wiederherstellung
(Probe)“ (`deploy/probe/sicherungsprobe.sh`) sichert über SFTP, löscht alles, stellt wieder her und
prüft Daten, Dateien und Hash-Kette.

## Überblick

| Was | Wann | Wie | Auf Unraid |
| --- | --- | --- | --- |
| Datenbank, laufend | jede Änderung, spätestens nach 60 Sekunden | WAL-G `wal-push`, verschlüsselt | `wal_005/` |
| Datenbank, voll | nachts 02:00 | WAL-G `backup-push`, verschlüsselt | `basebackups_005/` |
| Dateien (GAL, Scans, Notfallmappen) | nachts 02:00 | ein tar-Archiv, verschlüsselt | `dateien/` |
| Kettenköpfe | nachts 02:00 | unverschlüsselt, nur Prüfsummen | `kettenkopf/` |

- In der Datenbank stecken alle Vorgänge, signierten Prüfprotokolle, Zeitstempel und Konten.
- **Verschlüsselt** wird mit dem öffentlichen PGP-Schlüssel. Den privaten hat der Server nicht; ein
  gehackter Server kann alte Sicherungen also nicht lesen. Lesbar liegen auf Unraid nur die
  Kettenköpfe und die Verwaltungsangaben von WAL-G (Zeitpunkte, Größen, Dateinamen der Datenbank,
  keine Inhalte).
- **Aufbewahrung:** Der Server behält die letzten 30 Vollsicherungen (etwa 30 Tage) samt WAL und die
  letzten 30 Datei-Archive und löscht ältere. Die Monatsstände (12 Monate) halten die Snapshots
  auf Unraid, die der Server nicht löschen kann.
- **Datenverlust im Ernstfall:** Datenbank höchstens die letzte Minute; Dateien, die seit der
  letzten Nacht hochgeladen wurden (GAL, Scans), fehlen und müssen neu hochgeladen werden.
- **Löschfristen (#63):** Was `loeschfristen` löscht, verschwindet mit dem Ende der Aufbewahrung
  auch aus den Sicherungen. Nach einer Wiederherstellung `loeschfristen` einmal von Hand laufen
  lassen (Abschnitt 5).
- Die tägliche Prüfung der Sicherung ohne Schlüssel kommt erst mit Stufe 2 (Konzept).

### Sicherheitshinweis: SFTP ohne Prüfung des Host-Schlüssels

WAL-G (v3.0.9) prüft beim SFTP-Zugang den Host-Schlüssel des Servers **nicht**
(`pkg/storages/sh/storage.go`: `ssh.InsecureIgnoreHostKey()`). Darum:

- Unraid **nur über seinen Tailscale-Namen** ansprechen (`WALG_SSH_PREFIX=ssh://<unraid>.<tailnet>.ts.net/…`).
  WireGuard stellt dann sicher, dass die Gegenstelle wirklich Unraid ist.
- In der Tailscale-Richtlinie nur `psagent` → `<unraid>:22` erlauben.
- Mitlesen kann auch ein falscher Server nichts (PGP). Eine untergeschobene Sicherung fällt bei
  der Wiederherstellung auf: `kette_pruefen` mit dem Kettenkopf aus der Zeugen-Mail.

## 1. Schlüssel erzeugen (Felix, nicht Claude)

Auf einem vertrauenswürdigen Rechner, nicht auf dem Server:

```bash
gpg --quick-gen-key "PSAgent Sicherung <sicherung@verein.example>" default default never
gpg --armor --export sicherung@verein.example > public.asc
gpg --armor --export-secret-keys sicherung@verein.example > privat.asc
gpg --fingerprint sicherung@verein.example
```

- Passphrase: kennt der Admin; eine versiegelte Kopie liegt beim Vorstand (Konzept).
- `privat.asc` in zwei Kopien an getrennten Stellen im DAV: USB-Stick im Tresor und Ausdruck beim
  Vorstand. **Nie auf den Server.** Danach vom Rechner löschen.
- Fingerabdruck auf dem Ausdruck notieren.

## 2. Unraid vorbereiten (lokal)

- Eigener Share für die Sicherung auf einem Pool mit Snapshots (ZFS oder btrfs).
- Eigener Benutzer `psagent-sicherung`, nur SFTP (`ForceCommand internal-sftp`), nur mit
  Schlüssel; in `authorized_keys` kommt `ssh_key.pub` vom Server (Abschnitt 3).
- Snapshots täglich, aufbewahrt 30 Tage täglich und 12 Monate monatlich. Der SFTP-Benutzer darf
  Snapshots weder sehen noch löschen.
- Unraid im Tailnet; Richtlinie wie oben.

## 3. Server einrichten

Vor dem ersten Start der Datenbank (`server-einrichten.md`, Schritt 5), sonst meldet PostgreSQL bis
dahin fehlgeschlagene Archivierungen.

```bash
sudo install -d -m 755 /etc/psagent/sicherung
sudo install -m 644 public.asc /etc/psagent/sicherung/public.asc
sudo ssh-keygen -t ed25519 -N '' -C psagent-sicherung -f /etc/psagent/sicherung/ssh_key
# 999 ist der Benutzer postgres im DB-Container; nur er und root lesen den Schlüssel.
sudo chgrp 999 /etc/psagent/sicherung/ssh_key && sudo chmod 640 /etc/psagent/sicherung/ssh_key
cat /etc/psagent/sicherung/ssh_key.pub   # auf Unraid in authorized_keys eintragen
```

In `/etc/psagent/psagent.env` (Vorlage `deploy/psagent.env.example`):

```
WALG_SSH_PREFIX=ssh://<unraid>.<tailnet>.ts.net/<absoluter Pfad auf Unraid>/psagent
SSH_USERNAME=psagent-sicherung
SSH_PORT=22
```

Prüfen und erste Sicherung:

```bash
cd /opt/psagent/v2/deploy
sudo docker compose --env-file /etc/psagent/image.env exec -u postgres db wal-g st ls
sudo ./sicherung.sh
sudo docker compose --env-file /etc/psagent/image.env exec -u postgres db wal-g backup-list
sudo docker compose --env-file /etc/psagent/image.env exec -u postgres db \
    psql -Atc 'select archived_count, failed_count, last_failed_wal from pg_stat_archiver'
```

`failed_count` darf danach nicht mehr steigen. Timer einschalten (`server-einrichten.md`, Schritt 7):

```bash
sudo cp /opt/psagent/v2/deploy/systemd/psagent-sicherung.* /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now psagent-sicherung.timer
```

## 4. Im Betrieb

- Fehler stehen im Journal: `journalctl -u psagent-sicherung`. Das Skript endet dann mit 1, und
  Uptime Kuma meldet es sofort (`monitoring.md`). Ist die Vollsicherung gescheitert, wird nichts
  gelöscht. Ein hängendes WAL-Archiv meldet der stündliche Zustand.
- Ist Unraid nicht erreichbar, bleiben die WAL-Abschnitte auf dem Server, und PostgreSQL versucht es
  weiter. Bei langem Ausfall wächst `/var/lib/psagent/postgres/pg_wal`; Speicherplatz im Blick
  behalten.
- Vor jedem Update: `sudo ./sicherung.sh` (`server-einrichten.md`, Abschnitt 8).

## 5. Wiederherstellung

`deploy/wiederherstellen.sh` überschreibt nichts: Es bricht ab, wenn `postgres/` oder `media/`
unter `/var/lib/psagent` nicht leer sind oder PSAgent läuft. Es prüft zuerst Schlüssel und
Passphrase, holt die neueste Vollsicherung, spielt das WAL bis zum letzten Stand nach, holt das
neueste Datei-Archiv, migriert, startet und prüft die Hash-Kette. Am Ende steht die Dauer.

| Rückgabewert | Bedeutung |
| --- | --- |
| 0 | Wiederhergestellt, Kette unverändert |
| 1 | Kette verändert (Sicherheitsvorfall, `pruefwerkzeug.md`) oder ein Schritt ist gescheitert |
| 2 | Wiederhergestellt, aber etwas war nicht prüfbar (z. B. Zertifikat fehlt) |

### Probe auf dem Ersatzgerät

Einmal vor der Inbetriebnahme, ab Stufe 2 viermal im Jahr.

1. Ersatzgerät (z. B. ein Raspberry Pi 5, `server-einrichten.md`, Anhang) nach
   `server-einrichten.md`, Schritte 1–5, mit einem **anderen** Tailscale-Namen
   (z. B. `psagent-probe`). `psagent.env` und `public.asc` wie auf dem Server. Das
   `POSTGRES_PASSWORD` muss gleich sein; es steckt in der gesicherten Datenbank.
2. Eigenen SSH-Schlüssel für das Probegerät erzeugen (wie Abschnitt 3) und auf Unraid nur für
   die Probe eintragen.
3. USB-Stick mit `privat.asc` einstecken; den letzten Kettenkopf aus der Zeugen-Mail notieren
   (`<Nummer>:<Hash>`).
4. Wiederherstellen:

   ```bash
   cd /opt/psagent/v2/deploy
   sudo ./wiederherstellen.sh --probe /media/usb/privat.asc --kopf <Nummer>:<Hash>
   ```

   `--probe` legt `/etc/psagent/sicherung/probe` an. Solange es die Marke gibt, verwirft die
   Datenbank ihre WAL-Abschnitte, und `sicherung.sh` tut nichts; die Probe schreibt also nie in
   die echte Sicherung. Keine Timer einschalten.
5. Ergebnis und Dauer unten eintragen.
6. Aufräumen: `sudo docker compose --env-file /etc/psagent/image.env down`, dann
   `sudo find /var/lib/psagent/postgres /var/lib/psagent/media /var/lib/psagent/kettenkopf -mindepth 1 -delete`,
   den Probeschlüssel auf Unraid austragen, USB-Stick zurück in den Tresor.

### Ernstfall

1. Alten Server ausschalten und in der Tailscale-Verwaltung **entfernen**. Sonst schreiben zwei
   Server in dieselbe Sicherung.
2. Neues Gerät nach `server-einrichten.md`, Schritte 1–5, mit dem Namen **`psagent`** (Passkeys,
   QR-Etiketten und Links hängen daran). `psagent.env` aus dem Tresor bzw. vom alten Server.
3. Wiederherstellen:

   ```bash
   cd /opt/psagent/v2/deploy
   sudo ./wiederherstellen.sh --ernstfall /media/usb/privat.asc --kopf <Nummer>:<Hash>
   ```

   Danach sichert das Gerät die neue Zeitlinie der Datenbank und zieht sofort eine frische
   Vollsicherung.
4. `tailscale serve` und Timer einschalten (`server-einrichten.md`, Schritte 6.4 und 7), dann
   `sudo ./manage.sh loeschfristen --probelauf` und ohne `--probelauf`, falls es etwas löschen
   will.
5. Dateien, die am Tag des Ausfalls hochgeladen wurden, neu hochladen. USB-Stick zurück in den
   Tresor.

## Ergebnisse der Proben

| Datum | Gerät | Dauer | Ergebnis | Bemerkung |
| --- | --- | --- | --- | --- |
| | | | | |
