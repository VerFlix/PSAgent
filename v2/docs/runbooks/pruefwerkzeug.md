# Runbook: Prüfwerkzeug „unverändert?“

Gilt ab #19. Prüft, ob Prüfberichte und Hash-Kette unverändert sind. Welche Manipulationen das
findet, steht in `docs/manipulationen.md`.

## Wozu

- **Prüfknopf** am Prüfbericht (`/pruefung/berichte/`, jede Rolle): prüft einen Bericht.
- **`manage.py kette_pruefen`** (Admin, auf dem Server): prüft alles auf einmal,
  - die ganze Hash-Kette mit allen bekannten Köpfen,
  - die Zeitstempel der festgehaltenen Köpfe,
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

Täglich nach `kettenkopf` per Timer; ein Rückgabewert ungleich 0 geht ans Monitoring (#24).

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

Das Konzept sieht vor, auch unabhängig vom Pi zu prüfen. Dafür:

1. Den Code aus dem Repo nehmen (`git clone`), nicht vom Pi.
2. Eine Datenbanksicherung (#23) in ein lokales PostgreSQL einspielen. Die ZIP-Sicherung aus der
   Oberfläche reicht nicht: Sie enthält die Hash-Kette nicht.
3. Eigene Kopien der beiden Zertifikate aus Abschnitt 1 eintragen, `PSAGENT_TSA_URL` leer
   lassen (sonst meldet die Prüfung fehlende Stempel seit dem Stand der Sicherung).
4. Wie in Abschnitt 3 prüfen.

Die Wiederherstellung selbst probt Felix lokal (Wiederherstellungsprobe, Phase 4).

Ein einzelnes signiertes PDF ohne Datenbank prüft das Signier-Tool (`signieren pruefen`,
folgt in #19 Schritt 2).
