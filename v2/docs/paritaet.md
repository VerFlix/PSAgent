# Paritätsliste (Abnahmekatalog)

Jede Funktion des Altsystems (`psa_gui.py`, `verleih_gui.py`) bleibt erhalten oder wird besser.
Zeile 22 ist eine neue Anforderung. Zu jeder Zeile gehört mindestens ein automatischer Test;
Issues verweisen auf die Zeilennummer.

Quelle: Konzept „PSAgent 2.0 – Konzept“, Abschnitt 2 (Stand 14.10.2026).

| # | Bereich | Funktion heute | In PSAgent 2.0 |
| --- | --- | --- | --- |
| 1 | Stammdaten | Produkt anlegen: 9 Felder, nächste Prüfung, GAL-Datei/-Link, „keine PSA“; Einzelidentifikation (EI) Pflicht und eindeutig | Gleich, Eindeutigkeit zusätzlich per Datenbank-Index; neue Teile erst nach der ersten PSA-Prüfung verleihbar |
| 2 | Stammdaten | System anlegen: Name, fest 3 Teile, mindestens eine EI | Beliebig viele Teile |
| 3 | Stammdaten | Bearbeiten per Doppelklick mit Bestätigung; Schnellwahl +6M/+1J | Gleich; „nächste Prüfung“ nur noch über eine Prüfung änderbar |
| 4 | Stammdaten | GAL auf mehrere markierte Teile anwenden | Gleich; Datei wird nur einmal gespeichert statt je Teil kopiert |
| 5 | Dokumente | PDFs: Einsatzdokumentation, PD, Haftungsausschluss (Typst) | Gleich; Name und Stempel des Sachkundigen kommen aus dem Konto statt fest aus der Vorlage |
| 6 | PSA | Liste „Nächste Prüfungen“, gesperrte Teile oben, Vorgänge je Teil | Gleich, mit Filter und Zeitleiste je Teil |
| 7 | PSA | Prüfungsrunde: Teile sammeln, Ergebnis, Kommentar, nächste Prüfung, sperren/entsperren mit Pflichtkommentar, löschen = archivieren | Gleich, als Prüfmodus mit QR-Scan; Abschluss in einer Transaktion |
| 8 | PSA | Abschluss mit Name, Checkbox „Ich bin PSA-Sachkundig“ und gezeichneter Unterschrift | Signatur mit YubiKey + externer Zeitstempel; die Unterschrift bleibt als Bild sichtbar |
| 9 | PSA | Prüfberichte: Liste und Details mit Unterschrift | Gleich, plus Prüfknopf „unverändert?“ und signiertes PDF |
| 10 | PSA | Archiv ausgesonderter Teile mit Historie; Haftungsausschluss aus dem Archiv | Gleich, als Status „ausgesondert“ statt zweiter Datenbankdatei |
| 11 | Export | CSV oder PDF, alle oder markierte Teile, mit/ohne Archiv | Gleich, zusätzlich vollständiger Export als Sicherung |
| 12 | Verleih | Reservieren oder direkt ausleihen, mehrere Teile, Zeitraum, Filter nach Bezeichnung | Gleich, mit Datumsauswahl statt Texteingabe |
| 13 | Verleih | Verfügbar nur wenn nicht gesperrt, PSA aktuell (oder keine PSA), Rückgabe vor nächster Prüfung, keine Überschneidung | Gleich; Doppelbuchung zusätzlich in der Datenbank ausgeschlossen |
| 14 | Verleih | Ausleihe: Name, Adresse, E-Mail oder Telefon, ausgebende Person, Kurzkontrolle, GAL bereitgestellt, Unterschrift | Gleich; ausgebende Person = angemeldeter Nutzer; Entleiher wiederverwendbar |
| 15 | Verleih | Reservierung in Ausleihe umwandeln, mit denselben Prüfungen | Gleich |
| 16 | Verleih | Kontaktdaten nachtragen | Gleich |
| 17 | Verleih | Rückgabe: Kommentar, Kurzprüfung, rücknehmende Person, Unterschrift, optional sperren | Gleich |
| 18 | Verleih | Übersicht anstehender Ausleihen mit „überfällig“-Hinweis | Gleich, zusätzlich optionale E-Mail-Erinnerung |
| 19 | Verleih | Detailansicht mit beiden Unterschriften | Gleich |
| 20 | Verleih | Verfügbarkeitssuche (Bezeichnung, Name, Seriennummer, Zeitraum) und Monatskalender mit Tagesliste | Gleich, Kalender mit Monats- und Wochenansicht |
| 21 | Verleih | GAL öffnen (nur Windows) | GAL direkt im Browser, auf jedem Gerät |
| 22 | Notbetrieb | Verleih analog auf Papier (README), ohne Notfallliste und ohne Nacherfassung | Notfallmappe als PDF (Papier, Tablet, PC), Papierformulare mit Formularnummer, Nacherfassung mit Scan und Klärungsliste |
