# Runbook: Kettenkopf festhalten, stempeln und bezeugen

Gilt ab #15, #16 und #17. **Einrichten lokal mit Felix.**

## Wozu

Die Hash-Kette (#15) macht jede Änderung an Vorgängen erkennbar, aber nur, solange niemand die
ganze Kette neu berechnet. Dagegen hilft ein Beleg **außerhalb** des Servers:

- **Zeitstempel nach RFC 3161:** Ein externer Dienst signiert den Kettenkopf mit seiner Uhrzeit.
  Rückdatieren kann niemand; eine neu berechnete Kette hat für frühere Tage keine passenden
  Stempel.
- **Zeugen-Mail:** Kopf und Stempel gehen täglich an das Zeugen-Postfach des Vorstands.
- **Datei:** Kopf und Stempel liegen zusätzlich als Textdatei im Ordner, der auf Unraid
  gespiegelt wird.

Mail und Datei enthalten nur Nummer, Hash und Stempel, keine Personendaten.

## 1. Zeitstempeldienst wählen (lokal testen)

Kostenlose Dienste sind z. B. DigiCert, Sectigo oder FreeTSA. Für den gewählten Dienst:

1. Adresse eintragen: `PSAGENT_TSA_URL`.
2. Das Zertifikat der Zertifizierungsstelle des Dienstes von dessen Webseite laden, den
   Fingerabdruck mit der Angabe des Anbieters vergleichen und als PEM ablegen, z. B.
   `/etc/psagent/tsa-ca.pem`. Pfad in `PSAGENT_TSA_CA_PATH` eintragen. PSAgent vertraut nur
   dieser Stelle.
3. Probelauf: `uv run python manage.py kettenkopf`. Er muss „mit Zeitstempel“ melden.

## 2. Zeugen-Postfach und Ablage

- `PSAGENT_WITNESS_TO`: Postfach des Vorstands (#4), z. B. `zeuge@<domain>`; mehrere Adressen
  mit Komma. Der Versand läuft über Microsoft 365 (`docs/runbooks/mail-m365.md`).
- `PSAGENT_WITNESS_DIR`: Ordner für die tägliche Datei, z. B. `/var/lib/psagent/kettenkopf`;
  diesen Ordner mit dem Backup nach Unraid spiegeln.

## 3. Täglicher Timer

Einmal täglich `uv run python manage.py kettenkopf`, z. B. um 23:50 Uhr. Scheitert ein
Schritt, laufen die anderen trotzdem, und der Befehl endet mit Fehler (Monitoring, #24).

Fällt der Zeitstempeldienst einen Tag aus, geht der Kopf trotzdem per Mail raus. Der nächste
Stempel deckt alles davor mit ab. Ab 2 Tagen ohne gültigen Stempel meldet `kette_pruefen` einen
Befund.

## 4. Prüfen

- In PSAgent (Admin): `uv run python manage.py kette_pruefen`. Der Befehl prüft die Kette, alle
  festgehaltenen Köpfe und ihre Zeitstempel und alle Prüfberichte. Zeugen-Mails und Kopf-Dateien
  liest er mit `--zeugen <Datei oder Ordner>`, einzelne Köpfe gehen mit `--kopf <Nummer>:<Hash>`
  (beides mehrfach). Ablauf: `docs/runbooks/pruefwerkzeug.md`.
- Unabhängig von PSAgent (Vorstand): Jede Zeugen-Mail enthält den Stempel als Base64-Block und
  die OpenSSL-Befehle, um ihn gegen den Hash zu prüfen.
- Einmal im Quartal: die Zeugen-Mails des Quartals mit `--zeugen` prüfen
  (`docs/runbooks/pruefwerkzeug.md`, Abschnitt 3).
