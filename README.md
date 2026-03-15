# PSA Manager + Verleihplanung

Dieses Projekt besteht aus zwei getrennten GUIs mit klaren Rollen:

- **psa_gui.py** → für den **PSA-Sachkundigen**
- **verleih_gui.py** → für Mitarbeitende, die Produkte/Systeme **ausgeben und zurücknehmen**

Beide Skripte verwenden dieselbe SQLite-Datenbank (`psa.db`).

---

## Rollen und empfohlene Nutzung

## 1) PSA-Sachkundiger: `psa_gui.py` (Haupt- und Admin-Tool)

**Dafür ist es gedacht:**
- Produkte und Systeme anlegen/pflegen
- Doppelklick auf Produkt/System: vollständige Daten ansehen und bearbeiten (mit Bestätigungsabfrage)
- Feld `Nächste Prüfung am` inkl. Schnellwahl `+6M` / `+1J` und manueller Eingabe
- GAL-Dateien und Links verwalten
- PDF-Dokumente erzeugen (Einsatzdokumentation, PD, Haftungsausschluss)
- PSA-Prüfungsprozess mit Prüfungsrunde, Signatur des Sachkundigen und Berichtserstellung
- Entsperren gesperrter Produkte/Systeme **innerhalb der PSA-Prüfung** (mit Pflicht-Kommentar)

**Zusätzliche Tabs in `psa_gui.py`:**
- **Nächste Prüfungen**
   - Liste mit fälligen/kommenden Prüfungen
   - Gesperrte Produkte/Systeme stehen oben
   - Doppelklick öffnet Vorgangsfenster (Verleihvorgänge, Sperren/Entsperren, Kommentare)
   - Dort möglich: PSA-Prüfung dokumentieren, nächste Prüfung setzen, Haftungsausschluss erzeugen, optional löschen
   - Änderungen werden zunächst gesammelt (markiert) und erst mit `Prüfungsrunde bestätigen` final gespeichert
- **PSA-Prüfungsberichte**
   - Vollständige Berichtsübersicht
   - Doppelklick zeigt alle Details inkl. Unterschrift und aller betroffenen Produkte/Systeme (read-only)

**Wichtig:**
Die Entsperrung von gesperrten Produkten/Systemen ist absichtlich nur über die PSA-Prüfung in `psa_gui.py` möglich.

## 2) Ausgabe-/Rückgabe-Personal: `verleih_gui.py`

**Dafür ist es gedacht:**
- Verfügbarkeit für einen Zeitraum prüfen
- Reservieren oder direkt ausleihen
- Digitale Unterschrift erfassen
- Rückgabe mit Kommentar, Kurzprüfung und erneuter Unterschrift dokumentieren
- Optional: Produkt/System bei Rückgabe sperren (mit Pflicht-Kommentar)
- GAL-Prozess im Verleih:
   - Anzeige von GAL-Link/GAL-Datei
   - `GAL öffnen`
   - `GAL bereitgestellt` als Pflicht bei Ausleihe
- Doppelklick auf Verleih in der Tabelle öffnet Detailübersicht inkl. Unterschriften

---

## Nur `psa_gui.py` nutzen? Ja.

Wenn ihr **nur** mit `psa_gui.py` arbeiten wollt, ist das möglich.

Dann könnt ihr:
- alle Stammdaten verwalten (Produkte/Systeme)
- PDF-Dokumente erzeugen
- und den Verleihprozess **analog auf Papier** führen (Reservierung, Ausgabe, Rückgabe, Unterschriften)

Das bedeutet: digitales Verleih-Tracking ist optional, wenn ihr den Prozess organisatorisch analog abbildet.

---

## Voraussetzungen

- Python 3.10+
- (Optional, für PDF-Erzeugung) **Typst** installiert

Hinweis: Ohne Typst läuft die Datenpflege, aber PDF-Generierung ist nicht möglich.

### Voraussetzungen für eine virtuelle Umgebung (`venv`)

- Python ist installiert und im PATH verfügbar (`python --version` funktioniert)
- Das Projekt wurde lokal in einen Ordner geklont/kopiert
- Schreibrechte im Projektordner (für den `.venv`-Ordner)

---

## `venv` einrichten (empfohlen)

### 1) In den Projektordner wechseln

```powershell
cd \PSAgent
```

### 2) Virtuelle Umgebung erstellen

```powershell
python -m venv .venv
```

### 3) Virtuelle Umgebung aktivieren (Windows / PowerShell)

```powershell
.\.venv\Scripts\Activate.ps1
```

Falls PowerShell die Ausführung blockiert, einmalig in der aktuellen Sitzung erlauben:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

### 4) Abhängigkeiten installieren

Aktuell werden **keine zusätzlichen Python-Pakete** aus PyPI benötigt.
Die Anwendung nutzt nur Standardbibliothek-Module (z. B. `tkinter`, `sqlite3`, `json`, `pathlib`).

Optional für PDF-Erzeugung:
- **Typst** (separates Tool, kein `pip`-Paket)

Wenn eine `requirements.txt` vorhanden ist:

```powershell
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Wenn keine `requirements.txt` vorhanden ist, ist aktuell keine weitere `pip`-Installation nötig.

Optional prüfen, welche Pakete in der aktiven Umgebung installiert sind:

```powershell
pip list
```

### 5) GUI starten

```powershell
python psa_gui.py
```

oder

```powershell
python verleih_gui.py
```

### 6) `venv` verlassen

```powershell
deactivate
```

---

## Setup (Windows, kurz)

1. Projekt öffnen
2. Virtuelle Umgebung aktivieren
3. Abhängigkeiten (falls vorhanden) installieren
4. GUI starten

Beispiele:

- `psa_gui.py` starten
  - `python psa_gui.py`
- `verleih_gui.py` starten
  - `python verleih_gui.py`

Wenn ihr bereits eine `.venv` nutzt, dann den Python-Interpreter aus dieser Umgebung verwenden.

---

## Typischer Ablauf im Alltag

1. **PSA-Sachkundiger** pflegt Daten in `psa_gui.py`.
2. Ausgabe-Team arbeitet in `verleih_gui.py`:
   - Zeitraum wählen
   - verfügbare Produkte/Systeme laden
   - reservieren oder ausleihen
   - bei Ausleihe: Kurzkontrolle + GAL bereitgestellt + Unterschrift
3. Bei Rückgabe:
   - Rückgabe bestätigen
   - Kommentar + Kurzprüfung + Unterschrift erfassen
   - bei Bedarf sperren
4. Falls gesperrt:
   - der PSA-Sachkundige entsperrt im Tab **Nächste Prüfungen** in `psa_gui.py` (mit Kommentar)
5. PSA-Prüfungsrunde:
   - im Tab **Nächste Prüfungen** mehrere Objekte vorbereiten
   - gesammelt bestätigen
   - mit Name + Sachkundigen-Bestätigung + Unterschrift abschließen
   - Bericht wird automatisch im Tab **PSA-Prüfungsberichte** erzeugt

---

## Datenbank und Dateien

- Datenbank: `psa.db`
- PDFs: `PDF-Dokumente/`
- GAL-Dateien: `GAL/`
- Typst-Vorlagen: `Typst_Vorlagen/`

---

## Hinweise zur Sicherheit/Prozess

- Rollen strikt trennen (Admin vs. Ausgabe)
- Entsperren nur nach Prüfung durch PSA-Sachkundigen (in PSA-Prüfung)
- Kommentare bei Sperre/Entsperre immer nachvollziehbar dokumentieren
- Regelmäßig Backup von `psa.db` erstellen

---

## Troubleshooting (kurz)

## 1) Typst nicht gefunden / PDF wird nicht erstellt

- Ursache: Typst ist nicht installiert oder nicht im PATH.
- Lösung:
   - Typst installieren (z. B. via Winget, Scoop oder manuell).
   - Danach `psa_gui.py` neu starten.

## 2) Datenbank ist gesperrt (`database is locked`)

- Ursache: Mehrere Prozesse greifen gleichzeitig schreibend zu oder ein Prozess wurde unsauber beendet.
- Lösung:
   - Beide GUIs schließen.
   - Kurz warten und neu starten.
   - Prüfen, ob noch ein alter Python-Prozess läuft und diesen beenden.

## 3) Unterschrift fehlt

- Bei Ausleihe/Rückgabe ist die Unterschrift Pflicht.
- Lösung:
   - Im Signaturfenster mit gedrückter Maustaste unterschreiben.
   - Danach `Bestätigen` klicken.

## 4) Produkt/System kann nicht reserviert werden

- Mögliche Gründe:
   - Im Zeitraum bereits reserviert/ausgeliehen.
   - Nach Rückgabe gesperrt.
- Lösung:
   - Anderen Zeitraum wählen oder anderes Objekt nutzen.
    - Entsperrung nur über PSA-Prüfung in `psa_gui.py` durch den PSA-Sachkundigen mit Pflicht-Kommentar.

## 5) Prüfungsrunde lässt sich nicht bestätigen

- Mögliche Gründe:
   - Keine offenen markierten Änderungen vorhanden.
   - Name/Bestätigung/Unterschrift des PSA-Sachkundigen fehlt.
- Lösung:
   - Zuerst Objekte im Tab **Nächste Prüfungen** per Doppelklick in die Runde übernehmen.
   - Danach `Prüfungsrunde bestätigen` und Signaturdialog vollständig ausfüllen.
