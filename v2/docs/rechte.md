# Rechtetabelle

Stand der Umsetzung von Konzept, Abschnitt 5 (#12). Die Tabelle steht im Code an einer Stelle,
`accounts/rights.py` (`RIGHTS`). Diese Datei gibt sie wieder; ein Test (`tests/accounts/test_rights.py`)
schlägt fehl, wenn beide auseinanderlaufen; erzeugt wird sie mit
`accounts.rights.table_markdown()`.

| Recht | Admin | PSA-Sachkundig | Trainer/Materialwart | Verleiher | Viewer |
| --- | --- | --- | --- | --- | --- |
| Eigenes Konto und Startseite | ✓ | ✓ | ✓ | ✓ | ✓ |
| Verfügbarkeit und Kalender sehen (ohne Personendaten) | ✓ | ✓ | ✓ | ✓ | ✓ |
| Kontaktdaten der Entleiher sehen | ✓ | ✓ | nur laufende Ausleihen | nur laufende Ausleihen | – |
| Reservieren, ausgeben, zurücknehmen | ✓ | ✓ | ✓ | ✓ | – |
| Reservierungen anderer ändern oder stornieren | ✓ | ✓ | ✓ | nur eigene | – |
| Sperren, jederzeit (mit Pflichtkommentar) | ✓ | ✓ | ✓ | ✓ | – |
| Ausleihen nacherfassen (Notbetrieb) | ✓ | ✓ | eigene | eigene | – |
| Prüfprotokolle nacherfassen (Notbetrieb) | ✓, nur mit Sachkunde | eigene, nur mit Sachkunde | – | – | – |
| Teile anlegen, Beschreibung und GAL pflegen, Einsatzdokumentation erzeugen | ✓ | ✓ | ✓ | – | – |
| PSA-relevante Felder ändern, „keine PSA“ setzen, neues Teil freigeben | ✓, nur mit Sachkunde | ✓, nur mit Sachkunde | – | – | – |
| PD und Haftungsausschluss erzeugen (mit eigenem Namen und Stempel) | ✓, nur mit Sachkunde | ✓, nur mit Sachkunde | – | – | – |
| Export mit Personendaten | ✓ | ✓ | – | – | – |
| Prüfungsrunde, entsperren, aussondern, nächste Prüfung setzen | ✓, nur mit Sachkunde | ✓, nur mit Sachkunde | – | – | – |
| Prüfberichte sehen, signiertes PDF laden, auf Unverändertheit prüfen | ✓ | ✓ | ✓ | ✓ | ✓ |
| Klärungsliste nach dem Notbetrieb bearbeiten | ✓ | ✓ | – | – | – |
| Löschsperre für Entleiherdaten setzen und aufheben (mit Pflichtgrund) | ✓ | ✓ | – | – | – |
| E-Mail-Erinnerungen bei Überfälligkeit für alle ein- und ausschalten | ✓ | – | – | – | – |
| Nutzer, Rollen, Sachkunde-Zertifikate verwalten | ✓ | – | – | – | – |
| Signierte Einträge ändern oder löschen | niemand | niemand | niemand | niemand | niemand |

## Lesart

- Rollen bauen aufeinander auf: Admin > PSA-Sachkundig > Trainer/Materialwart > Verleiher > Viewer.
- „nur mit Sachkunde“: zusätzlich eine zum Zeitpunkt gültige Sachkunde (Qualifikation mit
  Ablaufdatum, nicht die Rolle).
- „nur laufende Ausleihen“: Kontaktdaten nur von Entleihern, die gerade etwas ausgeliehen
  und noch nicht zurückgegeben haben. Eine Reservierung zählt nicht; sonst ließe sich jede
  Adresse durch eine Reservierung auf den Namen freischalten (Befund der Sicherheitsprüfung,
  16.10.2026).
- „nur eigene“ / „eigene“: nur Vorgänge, die die Person selbst angelegt oder erfasst hat.
- „Eigenes Konto und Startseite“ steht nicht im Konzept; es beschreibt, was jede angemeldete
  Person darf (Startseite, eigene Passkeys).

## Wo geprüft wird

- **Dienste** (`inventory`, `inspections`, `lending`): `require(actor, Right…, obj)` vor jeder
  Änderung. Eine abgewiesene Anfrage ändert nichts. `actor=None` steht für die Übernahme aus dem
  Altsystem und Verwaltungsbefehle.
- **Ansichten**: jede trägt `requires(Right…)` oder `public`. Ein Test schlägt fehl, wenn eine URL
  keines von beiden hat. htmx-Teilanfragen werden genauso geprüft; ohne Anmeldung antworten sie
  mit `401` und `HX-Redirect` zur Anmeldung.
- **Öffentlich** sind nur Anmeldung (Passwort und Passkey), Abmeldung, der Einrichtungslink
  (Passkey oder, außer für Admin und PSA-Sachkundige, eigenes Passwort), `/healthz` und das
  Skript des Service Workers für die Offline-Kopie der Notfallmappe (`/notbetrieb/sw.js`, ohne
  Daten).

## Festlegungen, die das Konzept offenlässt

- Kontaktdaten ändern (Felix, 16.10.2026): Bei Reservierung und Ausgabe dürfen Verleiher
  Kontaktdaten eingeben oder überschreiben, ohne die bisherigen Werte zu sehen. Leere Felder
  bleiben unverändert. Angezeigt wird nur, was für die Ausgabe fehlt, und seit wann die Daten
  unverändert sind. Bei bekannten Entleihern verlangt die Ausgabe das Häkchen „Kontaktdaten
  seit TT.MM.JJJJ unverändert (nachgefragt)“ oder neue Angaben. Wer die Werte sehen und frei
  bearbeiten darf, richtet sich weiter nach „Kontaktdaten der Entleiher sehen“.
- Entleihersuche: nur der Name, ab 3 Zeichen, höchstens 10 Treffer.
- Export der Teileliste (CSV/PDF) enthält keine Personendaten und ist für jede Rolle erlaubt.
  Die vollständige Sicherung (ZIP mit allen Daten) nur für Admin (Felix, 16.10.2026), ohne
  Zugangsdaten; jeder Abruf wird protokolliert.
- Personendaten eines Entleihers nach Ablauf der Löschfrist löschen (Kontaktdaten, Name und
  Unterschrift, Scans seiner Ausleihformulare, #63): nur Admin (oder ein Verwaltungsbefehl).
  Nie bei offener Ausleihe, offenem Klärungsfall oder Löschsperre.
- E-Mail (#72, Felix, 17.10.2026): Quittungen sendet bei Ausgabe und Rückgabe, wer den Vorgang
  erfasst. Nachsenden darf, wer die Kontaktdaten des Vorgangs sehen darf, oder wer den Vorgang
  selbst erfasst hat, innerhalb von 24 Stunden. Erinnerungen für einen Vorgang schaltet ab
  Verleiher ab; für alle nur Admin.
- Löschsperre (Felix, 17.10.2026): Admin und PSA-Sachkundige, für einen konkreten Schadensfall
  oder Rechtsstreit, mit Pflichtgrund; Setzen und Aufheben werden nur angehängt.
- Eine Reservierung ausgeben, die jemand anderes angelegt hat, ist Verleihen, kein Ändern.
- Teile übernommener Altdaten (ohne `angelegt von`) gelten für Verleiher nicht als eigene.
- Vereinsgeräte festlegen und widerrufen gehört zu „Nutzer verwalten“ (nur Admin).
- Nutzer verwalten (#67): Ein Admin nimmt sich nicht selbst die Admin-Rolle und deaktiviert
  sich nicht selbst; das letzte aktive Admin-Konto bleibt erhalten. Rollenwechsel, Deaktivieren,
  Reaktivieren, Einrichtungslinks und vom Admin widerrufene Passkeys stehen im nur anhängbaren
  Protokoll `AccountEvent`.
- Briefkopf/Stempel (Felix, 16.10.2026): Den eigenen pflegt jede Person unter „Konto“, fremde
  nur der Admin in der Nutzerverwaltung. Jede Änderung steht mit dem neuen Text im Protokoll
  `AccountEvent`. Der Stempel erscheint auf PD und Haftungsausschluss (#35).
- PDF-Dokumente (Felix, 16.10.2026): PD und Haftungsausschluss tragen Name und Stempel der
  erzeugenden Person und sind darum an gültige Sachkunde gebunden. Die Einsatzdokumentation
  (Besitzer: Verein aus `PSAGENT_CLUB_NAME`) bleibt bei „Teile anlegen …“ ab Trainer.
- Notfallmappe (#52): Sie nennt Entleiher (nur mit Namen) und gehört darum zu „Reservieren,
  ausgeben, zurücknehmen“ (ab Verleiher). Erzeugen vergibt neue Formularnummern; der
  Verwaltungsbefehl `notfallmappe` (wöchentlicher Timer) läuft ohne Konto.
- Offline-Kopie der Notfallmappe (#52): Auf Vereinsgeräten lädt der Service Worker die neueste
  Mappe, solange jemand ab Verleiher angemeldet ist, und zeigt sie ohne Verbindung auch ohne
  Anmeldung an (Konzept: offline auf Vereins-Tablet und -PC). Auf anderen Geräten richtet man
  die Kopie nur von Hand ein.
- Nacherfassung (#52): Sperrungen erfasst nach, wer sperren darf (ab Verleiher); der Vorgang
  trägt die erfassende Person und das Datum vom Formular. Die Klärungsliste („Klärungsliste
  nach dem Notbetrieb bearbeiten“) sehen und bearbeiten nur PSA-Sachkundige und Admin, laut
  Konzept „für PSA-Prüfer oder Admin“, ohne Sachkunde-Pflicht.
- Ausgaben und Rückgaben nacherfassen (#52): ab Verleiher, wie Verleihen. Die Unterschriften
  stehen auf dem Papier (Herkunft „auf dem Papierformular“, Scan am Formular). Eine
  Doppelbuchung übernimmt nur, wer die Klärungsliste bearbeiten darf; dabei werden die
  überschneidenden Reservierungen storniert (Recht „Reservierungen anderer ändern“).
  Einen bekannten Entleiher (gleicher Name) verknüpft die Nacherfassung nur für Personen, die
  Kontaktdaten ohnehin sehen dürfen; Verleiher legen immer einen neuen Entleiher an. Sonst gäbe
  eine erfundene Ausgabe Einblick in fremde Kontaktdaten (wie bei Reservierungen ausgeschlossen).
- QR-Etiketten (#14): drucken gehört zu „Teile anlegen, Beschreibung und GAL pflegen …“ (ab
  Trainer). Der QR-Code enthält nur den Link `/q/<EI>`, keine Personendaten; der Link führt nach
  der Anmeldung zur Teileseite und ist für jede Rolle mit „Verfügbarkeit sehen“ erreichbar.
- Signaturzertifikate (#18): aufnehmen und widerrufen gehört zu „Nutzer, Rollen,
  Sachkunde-Zertifikate verwalten“ (nur Admin, ohne Sachkunde-Pflicht). Zertifikate gibt es nur
  für Konten ab PSA-Sachkundig; Verleiher bekommen keine (Felix, 21.10.2026). Die Ausgabe belegen
  persönliche Anmeldung, Unterschrift des Entleihers und Hash-Kette; signiert wird nur am PC,
  ausgegeben am Tablet. Ob eine Signatur gilt, entscheidet nicht die Rolle allein:
  Zertifikat zum Signaturzeitpunkt hinterlegt und nicht widerrufen, Rolle ab PSA-Sachkundig und
  gültige Sachkunde zu diesem Zeitpunkt. Ein Widerruf darf zurückdatiert werden (verloren seit),
  nicht vorausdatiert.
- Nutzerverwaltung mit YubiKey (#95, Felix, 21.10.2026): Jede Änderung unter „Nutzer, Rollen,
  Sachkunde-Zertifikate verwalten“ bestätigt der Admin einzeln mit einem YubiKey. Dazu gehören
  Konten, Rollen, Einrichtungslinks, Passkeys, Sachkunde, Stempel, Signaturzertifikate und
  Vereinsgeräte. Es zählt nur ein Passkey mit geprüfter Herstellerbescheinigung von Yubico; die
  Bestätigung gilt einmal und nur für die eine Adresse. Ohne YubiKey bleiben nur die
  Verwaltungsbefehle auf dem Server. Lesen braucht keine Bestätigung; die vollständige Sicherung (ZIP) schon, bei jedem
  Abruf (Felix, 21.10.2026).
  Ein YubiKey bestätigt erst nach der Freigabe: den ersten eines Kontos gibt der Server frei
  (`manage.py yubikey_freigeben`), jeden weiteren ein schon freigegebener YubiKey. Wer eine
  fremde Sitzung übernimmt, kann so keinen eigenen YubiKey eintragen; einen weiteren Passkey
  richtet ein Konto mit freigegebenem YubiKey nur mit dessen Bestätigung ein.
- Prüfprotokoll (#38, Felix, 21.10.2026): Das Protokoll einer abgeschlossenen Runde herunterladen
  und das signierte hochladen darf nur der Prüfer der Runde (mit „Prüfen“, also gültiger
  Sachkunde). Zertifikat, Rolle und Sachkunde gelten zum Zeitpunkt des Hochladens; die Uhrzeit
  in der Signatur zählt nicht. Ohne Zeitstempel wird nichts gespeichert. Abgelehnte Versuche
  stehen mit Grund bei der Runde (`SignatureRejection`).
- Prüfberichte (Parität 9, #39, Felix, 21.10.2026): Liste, Details, signiertes PDF und der
  Prüfknopf „Unverändert?“ für alle Rollen. Altberichte stehen markiert („Altbestand,
  ungesichert“) in derselben Liste, ohne Prüfknopf. Die Berichte nennen die sachkundige
  Person; auch Viewer sehen sie (Felix, 22.10.2026). Das ist eine bewusste Ausnahme vom
  Grundsatz „Viewer sehen keine Personendaten“: Der Name der prüfenden Person gehört zum
  Prüfbericht, Entleiherdaten bleiben für Viewer gesperrt. Der Prüfknopf
  beurteilt das Zertifikat im Rückblick: Zählt, was beim Hochladen galt; eine später widerrufene
  Sachkunde, eine entzogene Rolle oder ein gestrichener YubiKey machen alte Berichte nicht
  ungültig, ein auf vorher zurückdatierter Widerruf schon.
- Archiv ausgesonderter Teile (Parität 10, #40, Felix, 22.10.2026): gehört zu „Verfügbarkeit und
  Kalender sehen“ (jede Rolle). Wer ausgesondert hat, zeigt das Archiv erst ab Verleiher, wie das
  Recht „Reservieren, ausgeben, zurücknehmen“. Hinweis: Die Historie auf der Teileseite nennt
  Sperren und Aussondern bisher für jede Rolle mit Namen; nur die Entleiher blendet sie für
  Viewer aus.
