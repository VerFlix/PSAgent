# Import der Altdaten

Das Importwerkzeug (`legacy/importer.py`, Befehl `legacy_import`) übernimmt `psa.db`,
`psa_archiv.db` und den Ordner `GAL/` des Altsystems nach PSAgent 2.0 (#10). Den echten Import
macht nur Felix (#29). Claude arbeitet ausschließlich mit künstlichen Daten aus dem
Testdaten-Generator (CLAUDE.md, Regel 1).

## Ablauf für den echten Import

1. Kopien von `psa.db`, `psa_archiv.db` (falls vorhanden) und `GAL/` in einen Ordner legen.
   Das Werkzeug öffnet sie nur lesend, arbeitet aber trotzdem nur auf Kopien.
2. Probelauf gegen eine leere Datenbank:
   `uv run python manage.py legacy_import <ordner> --probelauf --bericht probelauf.md`
3. Bericht durchgehen. Abgelehnte Datensätze und Klärungspunkte im Altsystem bereinigen, neue
   Kopien ziehen, Probelauf wiederholen, bis der Bericht passt.
4. Echter Import in die leere Datenbank:
   `uv run python manage.py legacy_import <ordner> --bericht import.md`
   Alles läuft in einer Transaktion; bricht etwas ab, bleibt die Datenbank leer.

Der Import bricht ab, wenn die Zieldatenbank schon Teile, GAL-Dokumente, Entleiher,
Ausleihen, Prüfungsrunden oder Altberichte enthält. Benutzerkonten dürfen schon angelegt sein.

## Bericht

- SHA-256 von `psa.db`, `psa_archiv.db` und `GAL/` (über Name und Inhalt aller Dateien). Sie
  sind die Grundlage des Import-Siegels, das mit Signatur und Zeitstempel später dazukommt.
- Je Tabelle: gelesen, übernommen, abgelehnt, übersprungen (leere Bauteile, nicht mehr gültige
  Sperren). Jede gelesene Zeile steht in genau einer Spalte.
- Abgelehnt, Warnungen, Klärungspunkte, jeweils mit Grund.
- Datensätze stehen nur mit Nummer im Altsystem und EI im Bericht (`Produkt #12 (EI TST-0012)`,
  `Ausleihe #34`), nie mit Namen, Kontaktdaten oder Unterschriften. Ein Test prüft das.

## Was wohin geht

| Altsystem | PSAgent 2.0 |
| --- | --- |
| `products` | Teil (Produkt) mit einem Bauteil, `legacy_kind`/`legacy_id` = `product`/ID |
| `systems`, `system_parts` | Teil (System); Bauteile ohne jede Angabe fallen weg |
| Datumsfelder | `JJJJ-MM-TT` und `TT.MM.JJJJ` werden umgewandelt; Ungültiges wird leer (Warnung) |
| `herstellungsjahr` | Zahl 1900 bis 2200, sonst leer (Warnung) |
| `gal_datei` | GAL-Dokument (nur PDF, einmal gespeichert); fehlende Datei, absoluter Pfad: Klärungspunkt |
| `gal_link` | GAL-Link (nur http/https), sonst Klärungspunkt |
| `keine_psa = 1` | Vorgang „keine PSA“ gesetzt |
| `naechste_pruefung_am` | Übernahme-Runde „Altbestand übernommen“: Ergebnis „in Ordnung“ mit diesem Datum, auch wenn es verstrichen ist (dann fällig). Damit ist das Teil freigegeben. Ohne gültiges Datum und ohne „keine PSA“ bleibt es „neu, nicht freigegeben“ (Klärungspunkt). |
| `last_psa_*` | im Kommentar des Übernahme-Ergebnisses |
| `item_locks` | Vorgänge „gesperrt“ (und „entsperrt“) mit Zeitpunkt und Kommentar |
| `archived_items` + Teil aus `psa_archiv.db` | Teil mit Vorgang „ausgesondert“ (Zeitpunkt, Kommentar) |
| `verleih_planung` | je Zeile ein Ausleihvorgang mit einer Ausleihe, `legacy_id` = ID |
| `entleiher`, Kontaktfelder | Entleiher mit Kontaktdaten; gleicher Name und gleiche E-Mail oder Telefonnummer = derselbe Entleiher |
| `ausgebende_person`, `ruecknehmende_person` | „Person laut Altsystem“ am Vorgang |
| `signature_data`, `return_signature_data` | Unterschrift „Altbestand, ungesichert“ |
| leerer `status` | wie `ensure_verleih_schema`: mit Unterschrift „ausgeliehen“, sonst „reserviert“ (Warnung) |
| `psa_pruefungsberichte`, `…_items` | Altbericht, wörtlich, ohne Wirkung, mit Verweis auf das Teil |

Ausleihen und Prüfberichte stehen nach dem Archivieren in beiden Datenbanken. Gleiche IDs werden
nur einmal übernommen, die Hauptdatenbank gilt.

## Ablehnungen

| Fall | Folge |
| --- | --- |
| EI fehlt (auch bei einem Bauteil mit Angaben) | aktives Teil abgelehnt; ausgesondertes Teil bekommt Ersatz-EI `ALT-P<ID>-<Pos>` bzw. `ALT-S<ID>-<Pos>` |
| EI kollidiert nach Normalisierung (Groß-/Kleinschreibung, alle Leerzeichen) | alle beteiligten aktiven Teile abgelehnt; ausgesonderte bekommen eine Ersatz-EI |
| Feld länger als in PSAgent 2.0 erlaubt | Teil bzw. Ausleihe abgelehnt |
| Ausleihe zu einem abgelehnten oder unbekannten Teil | Ausleihe abgelehnt |
| Von- oder Rückgabedatum ungültig, Rückgabe vor Beginn | Ausleihe abgelehnt |
| Aktive Ausleihe überschneidet sich mit einer früheren aktiven desselben Teils | spätere Ausleihe abgelehnt |
| Aktive Ausleihe eines ausgesonderten Teils | Ausleihe abgelehnt |

## Abgleich alt gegen neu

`tests/legacy/test_import_comparison.py` importiert künstliche Datenbanken und fragt danach
Alt- und Neudaten mit denselben Fragen ab: Fälligkeitsliste (nächste Prüfung, Sperre),
Verfügbarkeit an fünf Stichtagen und Anzahl je Tabelle. Abweichungen sind nur dort erlaubt, wo
PSAgent 2.0 bewusst strenger ist (`docs/altsystem-verhalten.md`, #9):

- Rückgabe nach der nächsten Prüfung (im Altsystem nur in der Oberfläche geprüft)
- überfälliges Teil bleibt blockiert
- leerer Status blockiert im Altsystem nicht
- `TT.MM.JJJJ` vergleicht das Altsystem als Text
- eine abgelehnte, überschneidende Ausleihe
