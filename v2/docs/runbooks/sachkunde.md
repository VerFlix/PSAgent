# Runbook: Sachkunde läuft ab, verlängern oder widerrufen

Gilt ab #26. Die PSA-Sachkunde hat ein Ablaufdatum. Prüfen und Signieren hängen an ihr, nicht
an der Rolle (Konzept, Abschnitt 5).

## Was PSAgent tut

- **Hinweis 60 Tage vorher** auf der Startseite (Festlegung Felix, 04.11.2026):
  - die Person selbst: „Deine Sachkunde gilt noch bis …“,
  - der Admin: Liste „Sachkunde läuft ab“ mit allen aktiven Konten, abgelaufene zuerst.
  - Eine lückenlos anschließende Sachkunde schiebt das Ende hinaus; dann verschwindet der
    Hinweis. Widerrufene Einträge zählen nicht.
- **Nach dem Ablauf** (der Tag „gültig bis einschließlich“ ist vorbei) darf die Person nicht
  mehr:
  - prüfen und Prüfungsrunden signieren,
  - entsperren, aussondern, „keine PSA“ setzen, „nächste Prüfung“ setzen,
  - PSA-relevante Felder ändern (EI, Herstellungsjahr, Datum 1. Einsatz),
  - PD und Haftungsausschluss erzeugen,
  - Prüfprotokolle aus dem Notbetrieb nacherfassen.
  Ausgeben, sperren und alles andere ihrer Rolle geht weiter. Der Hinweis bleibt auf der
  Startseite, solange die Rolle ab PSA-Sachkundig ist.
- **Alte Prüfberichte bleiben gültig.** Zählt, ob die Sachkunde zum Zeitpunkt des Signierens galt
  (`docs/rechte.md`, Prüfberichte). Ein Ablauf später ändert daran nichts.
- **Signaturzertifikat:** bleibt hinterlegt. Mit neuer Sachkunde signiert die Person mit
  demselben YubiKey weiter (`zertifikate.md`).

## 1. Rechtzeitig verlängern (die Person)

1. Sobald der Hinweis erscheint: Fortbildung bzw. Lehrgang buchen, der die Sachkunde verlängert.
2. Den Nachweis (Teilnahmebescheinigung) dem Admin geben.

## 2. Neue Sachkunde eintragen (Admin, am PC mit YubiKey)

Nutzerverwaltung → Person → Abschnitt „Sachkunde“ → „Sachkunde eintragen“:

- **gültig ab:** der Tag nach dem bisherigen Ende, damit keine Lücke entsteht (oder das Datum
  aus dem Nachweis, wenn es später liegt; dann gilt in der Lücke keine Sachkunde),
- **gültig bis einschließlich:** laut Nachweis,
- **Nachweis:** Lehrgang, Anbieter, Datum, Nummer der Bescheinigung; keine weiteren
  Personendaten.

Einträge werden nie geändert: Verlängern heißt immer, einen **neuen** Eintrag anlegen. Jede
Änderung bestätigst du mit dem YubiKey (#95). Danach verschwindet der Hinweis bei der Person und
in deiner Liste.

## 3. Sachkunde abgelaufen und niemand darf prüfen

Läuft die Sachkunde aller PSA-Sachkundigen ab, kann niemand mehr prüfen. Teile, deren nächste
Prüfung vorbei ist, sind dann nicht mehr verleihbar (Fachregel). Gesperrte Teile bleiben
gesperrt, bis jemand mit Sachkunde entsperrt.

- Möglichst vermeiden: Fortbildungen so legen, dass sich die Sachkunden überlappen.
- Prüft vorübergehend eine sachkundige Person von außen, braucht sie ein Konto ab
  PSA-Sachkundig, eine eingetragene Sachkunde (Abschnitt 2) und ein Signaturzertifikat
  (`zertifikate.md`). Das legt der Admin an; danach Konto deaktivieren, Zertifikat widerrufen.

## 4. Sachkunde vorzeitig widerrufen

Wenn eine Sachkunde nicht mehr gelten darf (z. B. Nachweis zurückgezogen, Person scheidet aus):
Nutzerverwaltung → Person → bei der Sachkunde „Widerrufen“, mit Begründung (keine Namen
Dritter), bestätigt mit dem YubiKey. Der Widerruf gilt ab sofort; Prüfberichte davor bleiben
gültig. Scheidet die Person aus, außerdem ihr Signaturzertifikat widerrufen (`zertifikate.md`,
Abschnitt 5) und das Konto deaktivieren.
