# Runbook: Prüfwerkzeug „unverändert?“

Gilt ab #19. Prüft, ob Prüfberichte und Hash-Kette unverändert sind. Welche Manipulationen das
findet, steht in `docs/manipulationen.md`.

## Wozu

- **Prüfknopf** am Prüfbericht (`/pruefung/berichte/`, jede Rolle): prüft einen Bericht.
- **`manage.py kette_pruefen`** (Admin, auf dem Server): prüft alles auf einmal,
  - die ganze Hash-Kette mit allen bekannten Köpfen,
  - die Zeitstempel der festgehaltenen Köpfe,
  - jedes Bauteil gegen seinen festgehaltenen Stand (EI, Herstellungsjahr, Datum 1. Einsatz und
    die übrigen Felder, #20),
  - jeden signierten Prüfbericht wie der Prüfknopf,
  - auf Wunsch die Zeugen-Mails bzw. Kopf-Dateien (`--zeugen`) und die Papierliste der
    Fingerabdrücke (`--fingerabdruecke`).

Ergebnis und Rückgabewert:

| Ergebnis | Rückgabewert | Bedeutung |
| --- | --- | --- |
| unverändert | 0 | Alles geprüft, nichts gefunden |
| verändert | 1 | Mindestens ein Befund; die Meldungen nennen Tabelle, Nummer bzw. Bericht |
| nicht prüfbar | 2 | Etwas ließ sich nicht prüfen, z. B. fehlende Einstellung; der Grund steht dabei |

Ein Befund ist ein Sicherheitsvorfall: nichts ändern, Ausgabe aufheben, Vorstand informieren.

## 1. Einstellungen

Für einen vollständigen Lauf braucht der Server:

- `PSAGENT_TSA_CA_PATH`: Zertifikat der Zertifizierungsstelle des Zeitstempeldienstes
  (`docs/runbooks/kettenkopf.md`),
- `PSAGENT_YUBICO_CA_PATH`: Wurzel der Herstellerbescheinigung von Yubico
  (`docs/runbooks/zertifikate.md`).

Fehlt eine davon, endet der Lauf mit „nicht prüfbar“ und nennt die Einstellung.

## 2. Regelmäßig auf dem Server

```bash
uv run python manage.py kette_pruefen
```

Täglich nach `kettenkopf` per Timer; ein Rückgabewert ungleich 0 meldet Uptime Kuma sofort
(`docs/runbooks/monitoring.md`).

## 3. Einmal im Quartal mit dem Zeugen

Nur so fallen eine vollständig neu berechnete Kette und eine eingespielte ältere Sicherung auf
(`docs/manipulationen.md`, M5 und M6).

1. Der Vorstand speichert die Zeugen-Mails des Quartals und gibt sie auf einem USB-Stick:
   in Outlook im Web bzw. im neuen Outlook über „Herunterladen“ als `.eml`, im klassischen
   Outlook über „Speichern unter“ als reinen Text (`.txt`). Das Outlook-Format `.msg` liest der
   Befehl nicht. **Noch nicht mit dem echten Postfach erprobt** (lokal mit Felix).
2. Die Kopf-Dateien von Unraid (`kettenkopf-JJJJ-MM-TT-<Nummer>.txt`) gehen auch; Ordner werden
   rekursiv gelesen.
3. Die Papierliste der Fingerabdrücke als Textdatei abtippen oder vom Stick nehmen: je Zeile
   ein SHA-256-Fingerabdruck, mit oder ohne Doppelpunkte (wie `openssl x509 -fingerprint
   -sha256` ausgibt), `#` leitet Kommentare ein.
4. Prüfen:

   ```bash
   uv run python manage.py kette_pruefen \
     --zeugen /media/stick/zeugen-mails \
     --zeugen /mnt/unraid/kettenkopf \
     --fingerabdruecke /media/stick/fingerabdruecke.txt
   ```

Jeder Kopf aus den Dateien muss in der Kette stehen, sein Zeitstempel muss zum Hash passen und
die genannte Zeit tragen. Jedes Signaturzertifikat in PSAgent muss auf der Liste stehen.
Fingerabdrücke der Liste, die in PSAgent fehlen, nennt der Befehl nur als Hinweis.

Einzelne Köpfe gehen auch ohne Datei: `--kopf <Nummer>:<Hash>` (steht in jeder Zeugen-Mail).

## 4. Auf einem anderen Rechner

Das Konzept sieht vor, auch unabhängig vom Server zu prüfen. Dafür:

1. Den Code aus dem Repo nehmen (`git clone`), nicht vom Server.
2. Eine Datenbanksicherung (#23) in ein lokales PostgreSQL einspielen. Die ZIP-Sicherung aus der
   Oberfläche reicht nicht: Sie enthält die Hash-Kette nicht.
3. Eigene Kopien der beiden Zertifikate aus Abschnitt 1 eintragen, `PSAGENT_TSA_URL` leer
   lassen (sonst meldet die Prüfung fehlende Stempel seit dem Stand der Sicherung).
4. Wie in Abschnitt 3 prüfen.

Die Wiederherstellung selbst probt Felix lokal (Wiederherstellungsprobe, Phase 4).

## 5. Einzelnen Prüfbericht ohne Datenbank prüfen (Felix und Vorstand)

`signieren pruefen` aus dem Signier-Tool prüft ein signiertes PDF auf jedem Windows-PC, ohne
Server, ohne Datenbank, ohne Netz und ohne YubiKey:

- genau eine Signatur im Feld „Sachkundiger“, intakt, danach nur der Zeitstempel,
- der Fingerabdruck des Signaturzertifikats steht auf der Liste (`--fingerabdruecke`),
- der Zeitstempel ist gültig und stammt vom Dienst, dessen Zertifizierungsstelle angegeben ist
  (`--tsa-ca`).

Dazu zeigt es Runde, Prüfsumme des Inhalts und Kettenkopf aus dem signierten Teil. Ob
Ergebnisse, Sachkunde und Kette in PSAgent noch dazu passen, kann es ohne Datenbank nicht
wissen; dafür nennt es den passenden Befehl `kette_pruefen --kopf …` für den Server.
Ergebnis und Rückgabewert wie oben: unverändert (0), verändert (1), nicht prüfbar (2).

Einrichten (einmalig):

1. **uv** installieren: `winget install astral-sh.uv`
2. Das Repo holen: `git clone https://github.com/VerFlix/PSAgent.git`, ohne Git auf GitHub
   „Code“ → „Download ZIP“ und entpacken.
3. Im Repo-Ordner: `powershell -ExecutionPolicy Bypass -File .\v2\signiertool\installieren.ps1`
   (feste Versionen und Prüfsummen, #105). Das Yubico PIV Tool braucht man zum Prüfen nicht.
4. Die Zertifizierungsstelle des Zeitstempeldienstes als PEM-Datei ablegen, dieselbe wie
   `PSAGENT_TSA_CA_PATH` auf dem Server (`docs/runbooks/kettenkopf.md`, Abschnitt 1). Am besten
   selbst von der Webseite des Dienstes laden und den Fingerabdruck mit der Angabe dort
   vergleichen, nicht vom Server kopieren.
5. Die Papierliste der Fingerabdrücke als Textdatei abtippen (Format wie in Abschnitt 3).

Prüfen: Den Bericht in PSAgent unter „Prüfberichte“ → Bericht → „Signiertes Prüfprotokoll
(PDF)“ laden, dann

```powershell
signieren pruefen Pruefprotokoll_Runde_12_signiert.pdf `
  --tsa-ca C:\PSAgent\tsa-ca.pem --fingerabdruecke C:\PSAgent\fingerabdruecke.txt
```

Ohne `--tsa-ca` oder `--fingerabdruecke` lautet das Ergebnis „nicht prüfbar“; der Fingerabdruck
steht trotzdem da und lässt sich von Hand mit der Papierliste vergleichen.
