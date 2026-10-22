# Runbook: Signaturzertifikate der YubiKeys aufnehmen und widerrufen

Gilt ab #18. **Schlüssel erzeugen und PIN setzen: nur Felix. Erproben mit echtem YubiKey:
lokal (#21).** In der Cloud gibt es nur künstliche Test-YubiKeys.

## Wozu

Die Zertifikatsliste bestimmt, wessen Signatur der Server unter Prüfprotokollen annimmt (#38).
Sie enthält nur öffentliche Schlüssel, darf sich aber nicht unbemerkt ändern. Darum:

- Aufgenommen wird nur ein Zertifikat mit **Herstellerbescheinigung (Attestation) von Yubico**:
  Schlüssel im YubiKey erzeugt, Slot 9c, zugelassene Seriennummer, „PIN und Berührung bei jeder
  Signatur“.
- Aufnahme und Widerruf werden **nur angehängt** und stehen in der **Hash-Kette**. Die tägliche
  Zeugen-Mail an den Vorstand nennt die Fingerabdrücke aller Zertifikate.
- Beim Prüfen einer Signatur gleicht der Server die Liste mit ihren Kettengliedern ab und prüft
  die Herstellerbescheinigung erneut. Wurde die Liste an der App vorbei geändert, nimmt er keine
  Signatur an.

## 1. Einstellungen (einmalig)

1. Das Wurzelzertifikat der Yubico-PIV-Bescheinigung von `developers.yubico.com/PKI/` laden,
   den Fingerabdruck mit der Angabe von Yubico vergleichen und als PEM ablegen, z. B.
   `/etc/psagent/yubico-piv-ca.pem`. Pfad in `PSAGENT_YUBICO_CA_PATH` eintragen. Neuere
   YubiKeys (ab Firmware 5.7.4) haben eine längere Kette: Das Zwischenzertifikat von Yubico
   entweder in dieselbe Datei legen oder beim Aufnehmen mit hochladen.
2. Die Seriennummern der zugelassenen YubiKeys (`ykman list --serials`) durch Komma getrennt in
   `PSAGENT_YUBIKEY_SERIALS` eintragen. Ein YubiKey, der hier fehlt, signiert nicht mehr, auch
   wenn sein Zertifikat in der Liste steht.

Beides liegt außerhalb der Datenbank. Wer nur die Datenbank ändern kann, bringt so keinen
fremden Schlüssel unter.

## 2. Schlüssel und Zertifikat auf dem YubiKey (nur Felix)

Für jeden der beiden YubiKeys (Haupt- und Reserveschlüssel):

```bash
ykman piv keys generate --algorithm ECCP256 --pin-policy ALWAYS --touch-policy ALWAYS 9c pub.pem
ykman piv certificates generate --subject "CN=<Name des Sachkundigen>" --valid-days 1825 9c pub.pem
ykman piv certificates export 9c zertifikat.pem
ykman piv keys attest 9c bescheinigung-9c.pem
ykman piv certificates export f9 zwischenzertifikat-f9.pem
```

Die drei `.pem`-Dateien enthalten nur Öffentliches. Den SHA-256-Fingerabdruck notieren:

```bash
openssl x509 -in zertifikat.pem -noout -fingerprint -sha256
```

## 3. YubiKey als Passkey des Admins (einmalig, #95)

Jede Änderung in der Nutzerverwaltung bestätigt der Admin mit einem YubiKey. Dafür zählt nur ein
Passkey, dessen Herstellerbescheinigung (FIDO-Attestation) von Yubico stammt:

1. Das Wurzelzertifikat der FIDO-Bescheinigung von `developers.yubico.com/PKI/` laden, den
   Fingerabdruck vergleichen, als PEM ablegen und in `PSAGENT_YUBICO_FIDO_CA_PATH` eintragen.
2. Unter „Konto“ den YubiKey als Passkey einrichten. Der Browser fragt, ob die Website Marke
   und Modell des Schlüssels sehen darf: zulassen. Ein schon vorher eingerichteter Passkey zählt
   nicht; dann denselben YubiKey noch einmal einrichten (z. B. „YubiKey 1 (bescheinigt)“).
3. Den ersten YubiKey auf dem Server freigeben. Erst dann bestätigt er Änderungen; so kann
   niemand mit einer übernommenen Sitzung einen eigenen YubiKey unterschieben:

   ```bash
   uv run python manage.py yubikey_freigeben <Benutzername>            # zeigt die Kandidaten
   uv run python manage.py yubikey_freigeben <Benutzername> --passkey <Nummer>
   ```

   Nur den Passkey freigeben, den man eben selbst eingerichtet hat (Bezeichnung und Uhrzeit).
4. Den Reserve-YubiKey unter „Konto“ einrichten. Dabei fragt der Browser zuerst den schon
   freigegebenen YubiKey; der neue ist dann gleich freigegeben.

Ohne freigegebenen YubiKey geht in der Nutzerverwaltung keine Änderung; dann bleiben nur die
Verwaltungsbefehle auf dem Server.

## 4. Aufnehmen (Admin)

Nutzerverwaltung → Person (Rolle PSA-Sachkundig oder Admin) → „Signaturzertifikate“ →
Bezeichnung (z. B. „YubiKey 1“), dann die drei Dateien hochladen und mit dem YubiKey bestätigen. Der Server zeigt den
Fingerabdruck; er muss mit dem notierten übereinstimmen. Den Fingerabdruck auf Papier dem
Vorstand geben; die Zeugen-Mail am nächsten Tag muss ihn ebenfalls nennen.

Ein Zertifikat gilt nur zusammen mit gültiger Sachkunde der Person (Abschnitt „Sachkunde“).

## 5. Widerrufen

YubiKey verloren oder Person scheidet aus: Nutzerverwaltung → Person → „Widerrufen“, mit
Begründung. „widerrufen ab“ darf zurückliegen (seit wann verloren), nicht in der Zukunft.
Signaturen davor bleiben gültig, Signaturen ab dann nicht. Danach die Seriennummer aus
`PSAGENT_YUBIKEY_SERIALS` streichen. Ein widerrufener Schlüssel lässt sich nicht wieder
aufnehmen, auch nicht mit neuem Zertifikat.

## 6. Abgleichen

- Täglich: Die Zeugen-Mail nennt alle Fingerabdrücke. Der Vorstand vergleicht mit der
  Papierliste; ein unbekannter Fingerabdruck ist ein Sicherheitsvorfall.
- Jederzeit: `uv run python manage.py kette_pruefen` meldet Zeilen der Liste, die an der App
  vorbei geändert, gelöscht oder eingefügt wurden. Mit `--fingerabdruecke <Datei>` vergleicht er
  die Liste in PSAgent mit der abgetippten Papierliste (`docs/runbooks/pruefwerkzeug.md`).
