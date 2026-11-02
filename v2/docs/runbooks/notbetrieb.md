# Runbook: Notbetrieb mit der Notfallmappe

Gilt ab #52 (Parität 22). Prüfprotokolle nacherfassen kommt mit #80. Geübt wird der Notbetrieb
in #27. Fällt PSAgent aus, laufen Verleih und Prüfung auf Papier weiter; danach wird alles mit
Formularnummer und Scan nacherfasst.

## Überblick

| Was | Wo |
| --- | --- |
| Neueste Notfallmappe (PDF) | PSAgent → „Notbetrieb“, ab Verleiher; montags 06:00 neu (Timer `notfallmappe`) |
| Offline-Kopie | Vereinsgeräte automatisch; ohne Verbindung unter `/notbetrieb/mappe/neueste.pdf` |
| Ausgedruckte Mappe | festes Fach im DAV |
| Ausgefüllte Formulare | dasselbe Fach, bis sie nacherfasst sind; danach im Ordner (siehe 4.) |

Die Mappe enthält alle Teile (außer ausgesonderten) mit Status und nächster Prüfung, die
Ausleihen und Reservierungen der nächsten vier Wochen (Entleiher nur mit Namen) und leere
Formulare mit fortlaufender Nummer:

| Kürzel | Formular | Wer füllt aus |
| --- | --- | --- |
| A | Ausgabe und Rückgabe, je Ausgabe ein Blatt | ab Verleiher |
| S | Sperrung | ab Verleiher, mit Grund |
| P | Prüfprotokoll | nur PSA-Prüfer mit gültiger Sachkunde |

## 1. Vorbereiten (im Normalbetrieb)

1. **Offline-Kopie prüfen:** Auf jedem Vereinsgerät (Theke-Tablet, Vereins-PC) einmal
   angemeldet „Notbetrieb“ öffnen; dort muss „Offline gespeichert: <Datum>“ stehen. Lesezeichen auf
   `https://psagent.<tailnet>.ts.net/notbetrieb/mappe/neueste.pdf` anlegen. Auf anderen Geräten
   „Auf diesem Gerät offline bereithalten“ antippen oder die PDF speichern.
2. **Ausdrucken:** Die neueste Mappe ausdrucken und ins Fach legen. Vorschlag: einmal im Monat
   und nach größeren Änderungen am Bestand neu drucken.
3. **Alte Ausdrucke vernichten** (Aktenvernichter): Sie enthalten Namen von Entleihern.
   Unbenutzte Blätter einer alten Mappe nicht weiterverwenden.
4. **Üben** (#27): einmal im Jahr eine Ausgabe, eine Sperrung und eine Rückgabe auf Papier
   durchspielen und nacherfassen; dabei die Offline-Kopie im Flugmodus öffnen.

## 2. Wann gilt der Notbetrieb?

Wenn PSAgent nicht erreichbar ist und sich das nicht in wenigen Minuten beheben lässt. Vorher
kurz prüfen:

- Ist Tailscale auf dem Gerät an? Geht es von einem anderen Gerät?
- Meldet das Monitoring etwas (ntfy, „PSAgent erreichbar“)? Dann weiß der Admin schon Bescheid
  (`monitoring.md`, Abschnitt 5).
- Ist der Server selbst kaputt oder sind Daten verloren, stellt der Admin aus der Sicherung
  wieder her (`sicherung.md`, Ernstfall). Bis dahin läuft der Notbetrieb.

## 3. Im Notbetrieb

Die Regeln stehen auch auf dem Deckblatt der Mappe:

- Ausgegeben wird nur, was laut Mappe **nicht gesperrt** ist und dessen **nächste Prüfung nach
  dem Rückgabedatum** liegt. Teile mit „keine PSA“ brauchen keine Prüfung.
- Neue Teile („neu, nicht freigegeben“) werden nicht ausgegeben.
- **Für jede Ausgabe ein eigenes Formular A.** Teile mit EI, Entleiher mit Adresse und Telefon
  oder E-Mail, Zeitraum, beide Unterschriften. Die Rückgabe kommt auf dasselbe Blatt.
- **Sperren** auf Formular S, mit Grund. Entsperren und Aussondern nur der PSA-Prüfer auf dem
  Prüfprotokoll P.
- Neue Reservierungen nur vormerken; sie werden nach dem Ausfall geprüft und eingetragen.
- Kein Blatt wegwerfen. Verschriebene Blätter durchstreichen und behalten: Eine fehlende Nummer
  landet sonst auf der Klärungsliste.
- In Kommentarfelder keine Namen schreiben.
- Ausgefüllte Formulare kommen ins Fach.

## 4. Nach dem Ausfall: nacherfassen

Sobald PSAgent wieder läuft, möglichst **innerhalb von 7 Tagen**, unter „Notbetrieb“ →
„Nach dem Ausfall“. Reihenfolge:

1. **Sperrungen** (Formular S), damit gesperrte Teile nicht wieder ausgegeben werden. Erfassen
   darf jede Person ab Verleiher.
2. **Ausgaben und Rückgaben** (Formular A). Erfassen darf, wer verleihen darf: Verleiher und
   Trainer/Materialwart ihre eigenen Formulare, PSA-Sachkundige und Admin alle.
3. **Prüfprotokolle** (Formular P): der PSA-Prüfer selbst, mit Sachkunde und Signatur (#80).

Für jedes Formular:

- Formularnummer eingeben, wie aufgedruckt, z. B. `A-00042`.
- Scan hochladen: eine PDF oder Fotos (JPEG/PNG), die PSAgent zu einer PDF zusammenfasst. Der
  Scan kommt mit Prüfsumme in die Hash-Kette.
- Die Angaben mit dem **Datum vom Formular** eintragen. Der Vorgang trägt den Vermerk
  „nachgetragen von … am …“.
- Was gegen eine Regel verstieß, wird trotzdem eingetragen (das Teil ist ja draußen) und
  erscheint auf der Klärungsliste.

Danach das Papier abheften. Ausleihformulare enthalten Personendaten: aufbewahren, bis die
Löschfrist für alle Entleiher darauf abgelaufen ist, dann vernichten (`loeschfristen.md`).
Prüfprotokolle bleiben, denn maßgeblich ist das Papieroriginal.

## 5. Klärungsliste

PSA-Sachkundige und Admin bearbeiten sie unter „Notbetrieb“ → „Klärungsliste“. Jede Klärung
braucht einen Text und wird nur angehängt. Typische Fälle:

| Fall | Was tun |
| --- | --- |
| Formular … fehlt (Lücke in den Nummern einer Mappe) | Blatt suchen und nacherfassen; ist es verloren oder unbenutzt, das mit Begründung vermerken |
| Sperrung aus Formular … | Prüfen, ob das Teil zu Recht gesperrt ist; entsperren nur mit Sachkunde |
| Doppelbuchung: Ausgabe aus Formular … nicht eingetragen | Die überschneidende Reservierung stornieren und die Ausgabe übernehmen, oder die Ausgabe mit Begründung verwerfen |
| Ausgabe/Rückgabe aus Formular … prüfen | Z. B. gesperrtes Teil oder Prüfung überfällig: Teil prüfen lassen, Fall mit Ergebnis klären |

Die Liste sollte nach jedem Notbetrieb leer werden. Bleibt etwas offen, Felix informieren.
