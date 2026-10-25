# Runbook: Monitoring mit Uptime Kuma und ntfy

Gilt ab #24. **Einrichten und abnehmen lokal mit Felix** (Unraid, Pi, Handy). In der Cloud geprüft
sind die Skripte und Timer (`tests/test_monitoring.py`) und die Abfrage des WAL-Archivs gegen ein
echtes PostgreSQL 17, auch im Fehlerfall.

## Überblick

Uptime Kuma auf Unraid überwacht den Pi auf zwei Wegen und meldet per ntfy aufs Handy:

- **Von außen:** Uptime Kuma ruft `https://psagent.<tailnet>.ts.net/healthz` auf. Das zeigt, ob
  Pi, Tailscale, App und Datenbank laufen und ob das HTTPS-Zertifikat bald abläuft.
- **Herzschlag je Prüfung:** Jeder Timer auf dem Pi meldet nach seinem Lauf an eine eigene
  Push-Adresse in Uptime Kuma (`deploy/melden.sh`, `ExecStopPost=`). Erfolg heißt „up“, ein
  Fehler sofort „down“. Bleibt der Herzschlag aus (Pi aus, Timer aus), meldet Uptime Kuma das
  nach dem eingestellten Abstand.

| Was | Gemeldet von | Wann spätestens |
| --- | --- | --- |
| Pi, Netz, App oder Datenbank aus | Monitor „PSAgent erreichbar“ | nach etwa 3 Minuten |
| HTTPS-Zertifikat läuft ab | Monitor „PSAgent erreichbar“ | 14 Tage vorher |
| Sicherung gescheitert | Herzschlag „Sicherung“ | sofort |
| WAL-Archiv hängt (Unraid weg), Platte fast voll, Container ungesund | Herzschlag „Zustand“ | nach etwa einer Stunde |
| Zeugen-Mail oder Zeitstempel gescheitert | Herzschlag „Kettenkopf“ | sofort |
| Hash-Kette verändert oder nicht prüfbar | Herzschlag „Kette prüfen“ | sofort |
| Löschfristen, Erinnerungen, Notfallmappe gescheitert | je ein Herzschlag | sofort |
| Ein Timer läuft gar nicht | sein Herzschlag bleibt aus | nach seinem Abstand |

**Datenschutz:** Die Meldungen nennen nur Name und Ergebnis, z. B. „Fehler (exit-code,
Rückgabe 1); journalctl -u psagent-sicherung.service“, nie Personendaten. Sie gehen über ntfy,
also über einen fremden Dienst. Bei `ntfy.sh` kann jeder mitlesen, der das Thema kennt; darum ein
zufälliges Thema wählen (unten) oder ntfy selbst auf Unraid betreiben.

## 1. Uptime Kuma auf Unraid (lokal)

1. Uptime Kuma als App aus den Community Applications installieren (Port 3001), Konto anlegen.
   Unraid ist im Tailnet; Uptime Kuma nur dort erreichbar machen.
2. Tailscale-Richtlinie, zusätzlich zu `psagent` → `<unraid>:22` für die Sicherung:
   - `<unraid>` → `psagent:443` (Aufruf von `/healthz`)
   - `psagent` → `<unraid>:3001` (Herzschlag)
3. ntfy: Thema erzeugen, z. B. `psagent-$(openssl rand -hex 8)`, in der ntfy-App auf dem Handy
   abonnieren. In Uptime Kuma unter Einstellungen → Benachrichtigungen „ntfy“ anlegen (Server
   `https://ntfy.sh` oder der eigene, Thema, Priorität hoch), „Standardmäßig aktiviert“ und „Auf
   alle bestehenden Monitore anwenden“ setzen.

## 2. Monitore anlegen

| Monitor | Typ | Einstellungen |
| --- | --- | --- |
| PSAgent erreichbar | HTTP(s) | `https://psagent.<tailnet>.ts.net/healthz`, Intervall 60 s, 3 Wiederholungen, „Zertifikatsablauf melden“ an (Einstellungen: 14 Tage) |
| Zustand | Push | Abstand 3900 s (stündlich plus 5 Minuten) |
| Sicherung | Push | Abstand 90000 s (25 Stunden) |
| Kettenkopf | Push | 90000 s |
| Kette prüfen | Push | 90000 s |
| Löschfristen | Push | 90000 s |
| Erinnerungen | Push | 90000 s |
| Notfallmappe | Push | 608400 s (7 Tage plus 1 Stunde) |

Bei jedem Push-Monitor zeigt Uptime Kuma eine „Push URL“. Für den Pi wird nur der Teil vor dem
`?` gebraucht, z. B. `http://<unraid>.<tailnet>.ts.net:3001/api/push/AbCd1234`.

## 3. Pi einrichten

```bash
sudo install -m 600 /opt/psagent/v2/deploy/monitoring.env.example /etc/psagent/monitoring.env
sudo nano /etc/psagent/monitoring.env      # die sieben Push-Adressen eintragen
sudo cp /opt/psagent/v2/deploy/systemd/psagent-* /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now psagent-zustand.timer
sudo /opt/psagent/v2/deploy/zustand.sh     # einmal von Hand: „alles in Ordnung“
for name in zustand sicherung kettenkopf kette_pruefen loeschfristen erinnerungen notfallmappe; do
    sudo /opt/psagent/v2/deploy/melden.sh "$name"    # jeder Monitor wird grün
done
```

Die Push-Adressen sind Geheimnisse: Wer sie kennt, kann „alles in Ordnung“ melden. Die Datei
bleibt nur für root lesbar und kommt nie ins Repo.

Grenzwerte des Zustands (Standard in Klammern) über `sudo systemctl edit psagent-zustand.service`:

```
[Service]
Environment=PSAGENT_MIN_FREI_PROZENT=15
Environment=PSAGENT_MAX_WAL_WARTEND=10
```

## 4. Abnahme: Fehler gezielt auslösen

Abnahme von #24: Jeder Fehler führt zu einer Meldung auf dem Handy. Ergebnis unten eintragen.

1. **Dienst gestoppt:**

   ```bash
   cd /opt/psagent/v2/deploy
   sudo docker compose --env-file /etc/psagent/image.env stop app
   sudo systemctl start psagent-zustand.service   # Zustand: sofort rot
   ```

   Nach etwa 3 Minuten wird auch „PSAgent erreichbar“ rot. Zurück:
   `sudo docker compose --env-file /etc/psagent/image.env up -d app`, dann
   `sudo systemctl start psagent-zustand.service`. Beide Monitore werden wieder grün.
2. **Sicherung gescheitert:**

   ```bash
   sudo mv /etc/psagent/sicherung/ssh_key /etc/psagent/sicherung/ssh_key.weg
   sudo systemctl start psagent-sicherung.service  # Sicherung: sofort rot
   sudo mv /etc/psagent/sicherung/ssh_key.weg /etc/psagent/sicherung/ssh_key
   sudo systemctl start psagent-sicherung.service  # wieder grün
   ```

3. **Herzschlag bleibt aus** (optional, dauert etwa eine Stunde):
   `sudo systemctl stop psagent-zustand.timer`, warten, bis „Zustand“ rot wird, dann
   `sudo systemctl start psagent-zustand.timer`.

## 5. Bei einer Meldung

| Monitor | Erster Schritt |
| --- | --- |
| PSAgent erreichbar | `sudo docker compose --env-file /etc/psagent/image.env ps` und `logs app`. Sind nur Strom oder Netz weg, warten statt wiederherstellen; bis dahin Notbetrieb (Notfallmappe). Sonst `sicherung.md`, Ernstfall. |
| Zustand | `journalctl -u psagent-zustand -n 20` nennt den Grund (Container, WAL-Archiv, Platz) |
| Sicherung | `journalctl -u psagent-sicherung`; `sicherung.md` |
| Kettenkopf | Zeitstempeldienst und Mail prüfen; `kettenkopf.md` |
| Kette prüfen | **Sicherheitsvorfall:** nichts ändern; `pruefwerkzeug.md` |
| Löschfristen, Erinnerungen, Notfallmappe | `journalctl -u 'psagent-befehl@<befehl>.service'` |

## Ergebnisse der Abnahme

| Datum | Fehler | Meldung nach | Bemerkung |
| --- | --- | --- | --- |
| | | | |
