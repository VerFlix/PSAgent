# Runbook: Löschfristen für Entleiherdaten

Gilt ab #63. Die Löschfrist legt der Vorstand fest. Vorschlag (Felix, 17.10.2026): **3 Jahre
nach Ende des Jahres der letzten Ausleihe**. Beispiel: Die letzte Rückgabe war im Mai 2026, dann
wird am 1.1.2030 gelöscht.

Grundlage ist die regelmäßige Verjährung nach §§ 195, 199 Abs. 1 BGB. Ersatzansprüche wegen
Beschädigung einer geliehenen Sache verjähren schon nach 6 Monaten (§ 606 BGB). Die 30 Jahre
bei Körperschäden (§ 199 Abs. 2 BGB) rechtfertigen keine Speicherung aller Entleiher. Für einen
Unfall reichen Prüfprotokolle und Ausleihen ohne Namen; im konkreten Fall gibt es die
Löschsperre. Das ist keine Rechtsberatung.

## Was die App löscht

Der Befehl `uv run python manage.py loeschfristen` löscht je fälligem Entleiher:

- Kontaktdaten (Name, Adresse, E-Mail, Telefon)
- Name und Strichliste aller Unterschriften zu seinen Ausleihen
- die Scans seiner Ausleihformulare aus dem Notbetrieb. Ein Scan, auf dem noch andere Entleiher
  stehen, bleibt, bis auch diese gelöscht sind.

Es bleibt:

- die Ausleihhistorie mit Teil, Zeitraum, Kurzkontrolle und Prüfsummen; der Entleiher erscheint
  dann als „Entleiher #Nummer“
- ein Eintrag im Löschprotokoll mit Nummer, Datum und Anlass, ohne Namen

Nie gelöscht wird bei:

- offener Ausleihe oder Reservierung
- einem offenen Fall auf der Klärungsliste
- einer Löschsperre

## Einrichten (lokal mit Felix, auf dem Server)

1. Wert setzen: `PSAGENT_RETENTION_YEARS` (Standard 3). Der Wert muss zum Beschluss des
   Vorstands passen.
2. Nach einer Änderung eine neue Notfallmappe erzeugen; der Datenschutzhinweis steht auf jedem
   Ausleihformular.
3. Den ersten Lauf als Probelauf starten:
   `uv run python manage.py loeschfristen --probelauf`. Die Ausgabe nennt nur Nummern.
4. Einen täglichen Timer auf `uv run python manage.py loeschfristen` setzen, z. B. um 3 Uhr.
   Die Ausgabe landet im Journal und enthält keine Namen.

## Löschen auf Antrag (Art. 17 DSGVO)

`uv run python manage.py loeschfristen --entleiher <Nummer>` löscht sofort, außer bei offener
Ausleihe, offenem Klärungsfall oder Löschsperre. Die Nummer steht in der Detailansicht des
Ausleihvorgangs.

## Löschsperre (Art. 17 Abs. 3 lit. e, Art. 18 DSGVO)

Nur für einen konkreten Schadensfall oder Rechtsstreit, z. B. einen Unfall mit Anfrage der
Versicherung.

- **Setzen:** in der Detailansicht des Ausleihvorgangs, mit Pflichtgrund.
- **Aufheben:** unter „Datenschutz und Löschfristen“, sobald der Fall erledigt ist.
- **Wer:** Admin und PSA-Sachkundige.
- Setzen und Aufheben werden nur angehängt.

## Was der Verein selbst löschen muss

- **Papier der Notfallmappe:** Ausgefüllte Formulare aufbewahren, bis die Frist für alle
  Entleiher darauf abgelaufen ist. Danach mit dem Aktenvernichter vernichten.
- **Sicherungen:** Gelöschte Daten stehen weiter in älteren Sicherungen, bis diese rotiert sind.
  - Rotationsdauer festlegen und hier eintragen: ______
  - Nach einer **Wiederherstellung** sofort `loeschfristen` laufen lassen.
  - Entleiher, die seit dem Stand der Sicherung auf Antrag gelöscht wurden, mit
    `--entleiher` erneut löschen. Die Liste steht unter „Datenschutz und Löschfristen“, „Zuletzt
    gelöscht“; vor der Wiederherstellung notieren.
- **E-Mail (#72):** Quittungen und Erinnerungen werden ohne Kopie in „Gesendete Elemente“
  verschickt. Einmal im Jahr prüfen, dass das Postfach keine Kopien sammelt.
- **Freitext:** Kommentare bleiben dauerhaft, weil sie PSA-relevant sein können. Die Formulare
  bitten, keine Namen einzutragen. Steht doch ein Name darin, lässt er sich nicht entfernen,
  weil die Tabelle nur angehängt wird (Regel 3); dann mit Felix klären.
