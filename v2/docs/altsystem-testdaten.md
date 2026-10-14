# Altsystem: Schema-Varianten und Testdaten

Das Altsystem legt seine SQLite-Tabellen beim Programmstart an und rüstet fehlende Spalten per
`ALTER TABLE` nach. Nachgerüstete Spalten landen am Ende der Tabelle. Deshalb hängt das Schema
einer echten `psa.db` davon ab, mit welcher Programmversion sie angelegt und mit welcher sie
zuletzt geöffnet wurde. Der Testdaten-Generator (`v2/legacy/`) bildet alle Varianten nach, die
dabei entstehen können, und füllt sie mit künstlichen Daten. Echte Altdaten werden nie gelesen
(CLAUDE.md, Regel 1).

## Schema-Stände

| Stand | Commit | Neu gegenüber dem Vorgänger |
| --- | --- | --- |
| S1 | `93ec23f` | `products`, `systems`, `system_parts` (mit GAL-Datei und -Link) |
| S2 | `0480fd0` | `item_locks`, `verleih_planung` |
| S3 | `59d5b3c` | `psa_pruefungsberichte`, `psa_pruefungsbericht_items`; nächste/letzte PSA-Prüfung; `gal_provided_out` |
| S4 | `105fc0f` | `ausgebende_person`, `ruecknehmende_person` |
| S5 | `59ec978` | Archivdatenbank `psa_archiv.db` mit `archived_items` |
| S6 | `176ac49` | `last_psa_durchgefallen_am` |
| S7 | `b441ab6` | `keine_psa` (aktueller Stand, unverändert bis `4ba05f4`) |

## Varianten

| Variante | Entstehung | Archiv |
| --- | --- | --- |
| `s1` … `s7` | frisch mit dem jeweiligen Stand angelegt | ab `s5` |
| `s2_aktualisiert` | S2, danach mit dem aktuellen PSA-Programm geöffnet | – |
| `s3_aktualisiert` | S3 oder S4, danach mit dem aktuellen PSA-Programm geöffnet | – |
| `s5_aktualisiert` | S5, danach mit dem aktuellen PSA-Programm geöffnet | Stand S5 |
| `s6_aktualisiert` | S6, danach mit dem aktuellen PSA- oder Verleih-Programm geöffnet | Stand S6 |
| `s1_nur_verleih` | S1, danach nur mit dem aktuellen Verleih-Programm geöffnet | – |
| `s2_nur_verleih` | S2, danach nur mit dem aktuellen Verleih-Programm geöffnet | – |
| `s3_nur_verleih` | S3 oder S4, danach nur mit dem aktuellen Verleih-Programm geöffnet | – |
| `s5_nur_verleih` | S5, danach nur mit dem aktuellen Verleih-Programm geöffnet | Stand S5 |

- Das Verleih-Programm rüstet bei `products`/`systems` nur `naechste_pruefung_am` und
  `keine_psa` nach, nicht die `last_psa_*`-Spalten.
- Die Archivdatenbank entsteht erst beim ersten Archivieren und wird nur dabei nachgerüstet. In
  aktualisierten Varianten bleibt sie deshalb auf dem alten Stand.
- Weitere Kombinationen ergeben kein neues Schema (`s1_aktualisiert` = `s7` ohne Archiv,
  `s4_*` = `s3_*`, `s6_nur_verleih` = `s6_aktualisiert`). Startet man die Programme in anderer
  Reihenfolge, kann sich die Spaltenreihenfolge noch einmal ändern, nicht aber die Spaltenmenge.
  Der Import (#10) liest deshalb immer über Spaltennamen, nie über Positionen.
- Die Schema-Variante aus einer echten Altdatenbank (nur `.schema`, keine Daten) wird
  nachgereicht.

`tests/legacy/test_legacy_schema.py` führt bei jedem Testlauf die Schema-Funktionen des
jeweiligen historischen Commits aus und vergleicht das Ergebnis mit `legacy/schema.py`. Dafür
braucht der Checkout die volle Git-Historie.

## Testdaten

- 24 Produkte und 6 Systeme mit je drei Bauteilen (mit Randfällen 42 und 10), Sperren,
  Ausleihen (reserviert, verliehen, zurückgegeben), Prüfberichte und, bei Varianten mit Archiv,
  drei ausgesonderte Teile.
- Formate wie im Altsystem: Datum `JJJJ-MM-TT`, Zeitstempel `JJJJ-MM-TTThh:mm:ss`, Status
  `reserved`/`lent`/`returned`, Unterschriften als JSON-Strichliste, Beschriftungen wie
  `EI: … | PB: … | Name: … | SN: …`, GAL-Pfade unter Windows-Schreibweise `GAL\…`.
- Archivieren wie in `psa_gui.py`: Teil, Ausleihen, Sperre und betroffene Prüfberichte gehen in
  die Archivdatenbank. Aus der Hauptdatenbank verschwinden Teil, Bauteile und Sperre, die
  Ausleihen und Prüfberichte bleiben dort stehen.
- Erkennbar künstlich: Nachnamen wie „Beispiel“ oder „Muster“, E-Mail unter `example.org`,
  Telefonnummern mit Vorwahl `0000`, Anschrift in „00000 Beispielstadt“, erfundene Hersteller.
- Gleicher Startwert und gleicher Stichtag ergeben dieselben Daten.

### Randfälle (mit `--ohne-randfaelle` abschaltbar)

| Randfall | Beispiel |
| --- | --- |
| EI exakt doppelt | zweimal `TST-0900` (vor `a39ebdf` möglich) |
| EI mit anderer Großschreibung | `TST-0901` / `tst-0901` |
| EI mit Leerzeichen innen | `TST 0902` / `TST0902`: im Altsystem verschieden, in PSAgent 2.0 gleich |
| EI mit Leerzeichen außen | ` TST-0903` / `TST-0903 ` |
| EI bei Produkt und Bauteil | `TST-0904` |
| EI leer | Produkt ohne EI, System ohne jede EI |
| Datumsangaben | `01.03.2021`, `2021-02-30`, leer, Herstellungsjahr `unbekannt`, nächste Prüfung `31.12.2026` |
| „keine PSA“ mit Prüftermin | `keine_psa = 1` und `naechste_pruefung_am` gesetzt |
| GAL | Datei fehlt, nur Link, `/` statt `\`, absoluter Windows-Pfad, Dateien ohne Verweis |
| Systeme | nur ein Bauteil, Bauteile ohne Angaben |
| Ausleihen | überfällig, überschneidend, leerer Status, Rückgabe nach der nächsten Prüfung, gesperrtes Teil reserviert |

## Benutzung

```bash
uv run python manage.py legacy_testdb --liste
uv run python manage.py legacy_testdb --variant s5_aktualisiert --seed 42 --out /tmp/altdaten
```

Der Zielordner muss leer sein; vorhandene Datenbanken werden nie überschrieben. In Tests:
`legacy.testdata.build_legacy_db(tmp_path, "s7", seed=1)`.
