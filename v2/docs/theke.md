# Theke: jede Verleiher-Funktion erreichbar (#127)

An der Theke (Tablet, Vereinsgerät der Art „Theke“) gelten höchstens Verleiher-Rechte (#126).
Festlegung (Felix, 31.10.2026): „Den Verleihern fehlt keine Funktion.“ Diese Liste nennt zu
jeder Zeile der Rechtetabelle, die ein Verleiher hat, den Weg an der Theke. Der Test
`tests/counter/test_function_list.py` prüft, dass keine Zeile fehlt. Kommt ein neues Recht für
Verleiher dazu, gehört es hierher.

Eigene Touch-Seiten sind **fett** gesetzt. Alles andere öffnet die normale Ansicht; am
Theke-Gerät ist sie für Touch vergrößert (Knöpfe mindestens 48 px).

| Recht (Rechtetabelle) | Weg an der Theke |
| --- | --- |
| Eigenes Konto und Startseite | Startseite der Theke; „Mein Konto“ (normale Ansicht) |
| Verfügbarkeit und Kalender sehen (ohne Personendaten) | „Kalender“; in der Ausgabe nur freie Teile (**Ausgabe → Teile**) |
| Kontaktdaten der Entleiher sehen | nur laufende Ausleihen: „Vorgänge“ → Vorgang; an der Theke selbst nur Namen |
| Reservieren, ausgeben, zurücknehmen | **Ausgabe** (vier Schritte), **Reservierung abholen**, **Rückgabe**, **Heute**; „Reservieren“ (normale Ansicht) |
| Reservierungen anderer ändern oder stornieren | nur eigene: „Vorgänge“ → Vorgang → stornieren |
| Entleiher anlegen, auch ohne Ausleihe | **Ausgabe → Entleiher → Neue Person anlegen**; „Person anlegen“ |
| Mitgliedsausweis mit Entleiher verbinden | **Ausgabe → Entleiher** (Ausweis scannen, neue Person); „Mitgliedsausweis verbinden“ |
| Sperren, jederzeit (mit Pflichtkommentar) | **Schaden melden**; **Rückgabe** mit Schaden |
| Ausleihen nacherfassen (Notbetrieb) | „Notbetrieb“ → nacherfassen (eigene) |
| Prüfberichte sehen, signiertes PDF laden, auf Unverändertheit prüfen | „Prüfberichte“ |
| An der Theke anmelden (QR-Code + PIN oder Passwort), dort höchstens Verleiher-Rechte | Anmeldeseite des Theke-Geräts |
| Eigene PIN setzen, eigenen QR-Code drucken (Theke) | nur am PC: „Mein Konto“ (nicht in einer Sitzung per PIN, #126) |

Weitere Wege an der Theke:

- **Quittung erneut senden** und **Erinnerungen für einen Vorgang abschalten**: „Vorgänge“ →
  Vorgang (gehört zu „Reservieren, ausgeben, zurücknehmen“).
- **Notfallmappe**: „Notbetrieb“; auf Vereinsgeräten auch offline (#52).
- **Theke-Einstellungen** (Art-Kürzel, Schnellzugriff, nach Größe, Vorauswahl der Dauer): nur
  Admin mit YubiKey, also am PC; die Theke zeigt den Link nur dem Admin.
