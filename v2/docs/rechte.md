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
| Teile anlegen, Beschreibung und GAL pflegen, PDFs erzeugen | ✓ | ✓ | ✓ | – | – |
| PSA-relevante Felder ändern, „keine PSA“ setzen, neues Teil freigeben | ✓, nur mit Sachkunde | ✓, nur mit Sachkunde | – | – | – |
| Export mit Personendaten | ✓ | ✓ | – | – | – |
| Prüfungsrunde, entsperren, aussondern, nächste Prüfung setzen | ✓, nur mit Sachkunde | ✓, nur mit Sachkunde | – | – | – |
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
  (Passkey oder, außer für Admin und PSA-Sachkundige, eigenes Passwort) und `/healthz`.

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
- Kontaktdaten nach Ablauf der Löschfrist löschen: nur Admin (oder ein Verwaltungsbefehl).
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
