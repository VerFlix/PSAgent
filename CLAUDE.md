# CLAUDE.md – PSAgent

Software für Materialverleih und PSA-Prüfungen im DAV. Dieses Repo enthält zwei Dinge:

- **Altsystem** im Wurzelverzeichnis (`psa_gui.py`, `verleih_gui.py`, `generate_PDF.py`,
  `Typst_Vorlagen/`): Tkinter + SQLite. Nur Referenz, wird nicht geändert.
- **PSAgent 2.0** in `v2/`: Neubau als Web-App (Python 3.13, Django 6.1, PostgreSQL 17,
  HTMX). Hier findet die Arbeit statt.

Maßgeblich ist das Konzept „PSAgent 2.0 – Konzept“ (claude.ai-Dokument im Projekt „PSAgent“).
Der Abnahmekatalog liegt in `v2/docs/paritaet.md`.

## Arbeitsteilung – bei Abweichung zuerst Felix darauf hinweisen

Festgelegt am 14.10.2026. Passt eine Aufgabe nicht zu dem Ort, an dem du gerade arbeitest,
sag das Felix, bevor du anfängst, und nenne den vorgesehenen Ort.

| Wo | Was |
| --- | --- |
| Cloud-Sitzung mit Schreibrechten aufs Repo | Reine Software: Datenmodell, Import, Testdaten-Generator, Web-App, Rechteprüfung, Browser-Tests, Formulare, Notfallmappe, Runbooks, CI, Issues, Nachbessern bei Test-Fehlern |
| Lokal (Claude Code auf Felix' Rechner) | Hardware, Netz, Geheimnisse: YubiKey-Signatur und Signier-Tool erproben, Pi einrichten und ausrollen, Backups nach Unraid, Wiederherstellungsprobe, Zeitstempeldienste testen |
| Nur Felix, ohne Claude | Schlüssel und PINs erzeugen, echte Altdaten importieren (Claude liest sie nicht), Pull Requests prüfen und mergen, Branch-Schutz einstellen |

## Harte Regeln

1. **Keine echten Personendaten.** Nur künstliche Testdaten aus dem Testdaten-Generator.
   Echte Datenbanken (`*.db`, `*.sqlite3`) und Exporte niemals öffnen oder einlesen.
2. **Keine Geheimnisse ins Repo.** Keine Schlüssel, Zertifikate, Passwörter, `.env`.
   Konfiguration über Umgebungsvariablen; Vorlagen heißen `*.example`.
3. **Signierte und protokollierte Daten nur anhängen.** Tabellen für Prüfprotokolle,
   Hash-Kette und Vorgänge bekommen nie `UPDATE` oder `DELETE`. Korrekturen sind neue
   Einträge mit Verweis auf den alten.
4. **Eingecheckte Migrationen nicht ändern.** Immer eine neue Migration anlegen.
5. **Rechte im Server prüfen,** nicht nur in der Oberfläche. Für jede Zeile der
   Rechtetabelle gibt es einen Test, der auch den verbotenen Fall prüft.
6. **Keine eigene Kryptografie.** Nur etablierte Bibliotheken: `cryptography`, `pyHanko`,
   `py_webauthn`. Krypto-Änderungen im Pull Request ausdrücklich kennzeichnen.
7. **Altsystem nicht ändern.** Es dient als Referenz und für Tests, die sein Verhalten
   festschreiben.

## Fachregeln, die nie verletzt werden dürfen

- Verleihbar ist ein Teil nur, wenn es nicht gesperrt ist, die PSA-Prüfung aktuell ist
  (oder „keine PSA“ gesetzt ist) und die Rückgabe vor der nächsten Prüfung liegt.
  Überschneidende Ausleihen desselben Teils sind ausgeschlossen, auch in der Datenbank.
- Neue Teile sind erst nach der ersten PSA-Prüfung verleihbar (Status „neu, nicht freigegeben“).
- Entsperren, Aussondern, „keine PSA“ setzen, PSA-relevante Felder ändern (EI,
  Herstellungsjahr, Datum 1. Einsatz) und „nächste Prüfung“ setzen: nur mit gültiger Sachkunde.
- Sperren darf jede Rolle ab Verleiher, jederzeit, mit Pflichtkommentar.
- Die EI ist über alle Teile und Bauteile eindeutig (ohne Groß-/Kleinschreibung und Leerzeichen).

## Rollen und Qualifikation

Rollen bauen aufeinander auf: Admin > PSA-Sachkundig > Trainer/Materialwart > Verleiher > Viewer.
Prüfen und Signieren hängt zusätzlich an der Qualifikation **Sachkunde** (mit Ablaufdatum),
nicht an der Rolle. Details: Konzept, Abschnitt 5.

## Fachbegriffe

| Begriff | Bedeutung |
| --- | --- |
| PSA | Persönliche Schutzausrüstung (hier gegen Absturz) |
| PSA-Sachkundiger / PSA-Prüfer | Person mit Sachkunde, prüft und signiert |
| EI | Einzelidentifikation, eindeutige Kennung eines Teils oder Bauteils |
| GAL | Gebrauchsanleitung des Herstellers (Datei oder Link) |
| PD | Produktdatenblatt mit Prüfhistorie |
| Teil / Produkt / System | Ein Teil ist ein Produkt (1 Bauteil) oder ein System (mehrere Bauteile) |
| Prüfungsrunde | Sammlung von Prüfergebnissen, die gemeinsam signiert wird |
| Sperre / Aussonderung | Teil vorübergehend nicht verleihbar / endgültig aus dem Bestand |
| Hash-Kette, Kettenkopf | Verkettete Prüfsummen aller Vorgänge; der Kopf geht täglich an den Vorstand (Zeuge) |
| Notfallmappe, Nacherfassung | Papier-Notbetrieb bei Ausfall und spätere Erfassung mit Scan |

## Konventionen

- Code, Bezeichner und Docstrings: Englisch für Bezeichner, Deutsch für Docstrings und
  Oberflächentexte.
- Branches: `feature/<Issue-Nr>-<kurzname>`, Pull Requests nach `v2`. Nie direkt auf `v2`
  oder `master` pushen.
- Commits im Stil `feat: …`, `fix: …`, `chore: …`, `docs: …`, `test: …`.
- Tests zuerst; kleine Pull Requests; jede Paritätszeile hat mindestens einen Test.

## Befehle (in `v2/`)

```bash
docker compose up -d db
uv sync
uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run mypy .
uv run python manage.py makemigrations
```

Hooks in `.claude/settings.json` blockieren Änderungen an Schlüsseldateien, Datenbanken,
dem Altsystem und eingecheckten Migrationen und formatieren Python-Dateien in `v2/` mit ruff.
