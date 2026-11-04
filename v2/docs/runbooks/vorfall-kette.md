# Runbook: Befund in der Hash-Kette oder Kettenkopf weicht ab

Gilt ab #26. Für den Fall, dass `kette_pruefen` einen **Befund** meldet (Rückgabewert 1,
„verändert“), ein Kettenkopf nicht zur Zeugen-Mail passt oder die Zeugen-Mail einen unbekannten
Fingerabdruck nennt. Hintergrund: `docs/manipulationen.md`, Werkzeug:
`docs/runbooks/pruefwerkzeug.md`.

Ein Befund ist ein **Sicherheitsvorfall**, bis das Gegenteil feststeht. Keine Namen, Standorte
oder Einzelheiten zum Vorfall ins öffentliche Repo (Issues, Commits); Notizen bleiben beim
Vorstand.

## 0. Ist es überhaupt ein Befund?

| Meldung | Rückgabewert | Was tun |
| --- | --- | --- |
| „unverändert“ | 0 | nichts |
| „nicht prüfbar“, z. B. fehlende Einstellung, Zeitstempeldienst nicht erreichbar | 2 | **kein** Vorfall: Ursache beheben (Einstellung, `kettenkopf.md`, `monitoring.md`), dann erneut prüfen |
| „verändert“ mit Tabelle, Nummer oder Bericht | 1 | dieses Runbook |
| Kopf aus der Zeugen-Mail fehlt oder hat einen anderen Hash (`--zeugen`, `--kopf`) | 1 | dieses Runbook |
| Unbekannter Fingerabdruck in der Zeugen-Mail (`zertifikate.md`, Abschnitt 6) | – | dieses Runbook |

## 1. Sofort (Admin, ohne Rückfrage)

1. **Nichts ändern, nichts löschen, nichts „reparieren“.** Keine Migration, kein Update, kein
   Neustart, keine Korrektur in der Datenbank.
2. **Befund festhalten:** den Lauf wiederholen und die Ausgabe in eine Datei schreiben, mit
   Datum im Namen:

   ```bash
   sudo /opt/psagent/v2/deploy/manage.sh kette_pruefen 2>&1 | tee ~/befund-$(date +%F-%H%M).txt
   ```

   Die Datei auf einen USB-Stick kopieren; sie enthält Tabellen, Nummern und Hashes, keine
   Personendaten.
3. **Beweise erhalten:** Timer anhalten, die alte Stände löschen würden:

   ```bash
   sudo systemctl stop psagent-sicherung.timer psagent-loeschfristen.timer
   ```

   `sicherung.sh` löscht sonst Vollsicherungen über die 30 neuesten hinaus, `loeschfristen`
   Personendaten nach Frist. Die Snapshots auf Unraid (30 Tage täglich, 12 Monate monatlich,
   `sicherung.md`) bleiben unberührt. Die übrigen Timer laufen weiter; `kettenkopf` hält
   weiterhin täglich den Kopf fest.
4. **Zeitpunkt notieren:** wann der Befund auffiel, durch wen (Timer, Monitoring, Quartalsprüfung).
5. **Vorstand informieren** (Zeugen-Postfach bzw. direkt), mit der Datei aus Schritt 2.

## 2. Entscheidung des Vorstands: Weiterbetrieb oder Notbetrieb

Festlegung (Felix, 04.11.2026): **Der Vorstand entscheidet im Einzelfall.** Beide Wege:

| | Weiterbetrieb | Notbetrieb |
| --- | --- | --- |
| Verleih | läuft in PSAgent weiter | auf Papier mit der Notfallmappe (`notbetrieb.md`) |
| Vorteil | kein Mehraufwand, keine Nacherfassung | nichts Neues hängt an einer fraglichen Kette |
| Nachteil | neue Einträge hängen an einer Kette, deren Zustand unklar ist | Nacherfassung danach, Klärungsliste |
| Passt eher, wenn | der Befund eng begrenzt und erklärbar wirkt, z. B. ein einzelnes Bauteil, ein Zeitstempel | unklar ist, was verändert wurde, mehrere Tabellen betroffen sind oder Zugriff von außen möglich scheint |

Beim Notbetrieb: PSAgent für Änderungen nicht mehr nutzen; lesen (Teileliste, Prüfberichte)
bleibt erlaubt. Die Notfallmappe vom letzten Montag gilt, `notbetrieb.md` ab Abschnitt 3.

**Datenschutz:** Ist nicht auszuschließen, dass jemand unbefugt an Personendaten kam oder sie
verändert hat, prüft der Vorstand, ob eine Meldung an die Aufsichtsbehörde nötig ist
(Art. 33 DSGVO: binnen 72 Stunden nach Bekanntwerden). Das entscheidet der Vorstand, nicht der
Admin.

## 3. Klären (Admin mit Vorstand)

1. **Was meldet `kette_pruefen` genau?** Tabelle, Nummer, betroffener Bericht oder Kopf. Die
   Zeilen in `docs/manipulationen.md` (M1–M23) sagen, welche Art Änderung das ist und wo die
   Grenzen der Prüfung liegen.
2. **Mit dem Zeugen vergleichen:** die Zeugen-Mails und Kopf-Dateien des Zeitraums prüfen
   (`pruefwerkzeug.md`, Abschnitt 3). Passt der letzte Kopf vor dem Befund, liegt die Änderung
   danach.
3. **Unabhängig nachprüfen:** eine Sicherung von vor dem Befund auf einem anderen Rechner
   einspielen und dort prüfen (`pruefwerkzeug.md`, Abschnitt 4). Stimmt sie, lässt sich die
   Änderung eingrenzen. Signierte Prüfberichte einzeln mit `signieren pruefen` prüfen
   (`pruefwerkzeug.md`, Abschnitt 5).
4. **Ursache suchen:** Wer hatte Zugang zum Server und zur Datenbank (Tailscale-Geräte,
   SSH-Schlüssel, Admin-Konten)? Ein Zugriff an der App vorbei ist die naheliegende Ursache;
   die App selbst kann die Kette nicht verändern.

## 4. Abschluss

Je nach Ergebnis, vom Vorstand entschieden:

- **Erklärbar und harmlos**, z. B. ein Prüffehler: Ursache beheben (eigenes Issue ohne
  Einzelheiten), `kette_pruefen` muss wieder „unverändert“ melden.
- **Daten verändert:** auf den Stand von vor der Änderung wiederherstellen
  (`sicherung.md`, Ernstfall, auf einem frischen Gerät), dann alles danach Geschehene vom Papier
  bzw. aus dem Notbetrieb nacherfassen. Was nach dem Stand der Sicherung in PSAgent erfasst wurde,
  geht dabei verloren und muss bekannt sein, bevor man sich so entscheidet.
- Zugänge erneuern, die betroffen sein könnten (SSH-Schlüssel, Tailscale-Geräte, Passwörter,
  YubiKeys widerrufen nach `zertifikate.md`).

Danach:

1. Timer wieder einschalten:
   `sudo systemctl start psagent-sicherung.timer psagent-loeschfristen.timer`.
2. Notbetrieb beenden und nacherfassen (`notbetrieb.md`, Abschnitt 4).
3. Kurz festhalten, was passiert ist und was sich ändert, beim Vorstand; Verbesserungen als
   Issues ohne Einzelheiten.
