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

1. Die Zertifikate der Yubico-Bescheinigung laden.
   - Die Seite `developers.yubico.com/PKI/` war zuletzt nicht erreichbar. Maßgeblich ist die Liste.
   - Yubico führt alle Wurzelzertifikate in der Liste
     `https://developers.yubico.com/PKI/yubico-ca-certs.txt`. Die Liste ist mit OpenPGP signiert;
     die Signatur liegt daneben als `.sig`, Schlüssel und Anleitung stehen unter
     `https://developers.yubico.com/Software_Projects/Software_Signing.html`.
   - Die Zwischenzertifikate stehen in `yubico-intermediate.pem`.
   - Die PIV-Bescheinigung endet je nach Firmware an einer anderen Wurzel: bis 5.7.3 an
     „Yubico PIV Root CA Serial 263751“, ab 5.7.4 an „Yubico Attestation Root 1“, über
     Zwischenzertifikate. Die Firmware zeigt `ykman info`.

   Auf dem Server laden und die Fingerabdrücke anzeigen:

   ```bash
   mkdir -p /tmp/yubico && cd /tmp/yubico
   for f in yubico-piv-ca-1.pem yubico-ca-1.pem yubico-fido-ca-1.pem yubico-fido-ca-2.pem \
            yubico-intermediate.pem; do
     curl -fsSLO "https://developers.yubico.com/PKI/$f"
   done
   for f in yubico-piv-ca-1.pem yubico-ca-1.pem yubico-fido-ca-1.pem yubico-fido-ca-2.pem; do
     openssl x509 -in "$f" -noout -subject -fingerprint -sha256
   done
   ```

   Die Fingerabdrücke müssen mit den Zertifikaten in der Liste übereinstimmen (Stand der Liste
   03.02.2025):

   | Datei | Name | SHA-256 |
   | --- | --- | --- |
   | `yubico-piv-ca-1.pem` | Yubico PIV Root CA Serial 263751 | `63:EC:E9:14:E5:4D:D8:79:15:F3:40:33:C8:5A:F4:C0:69:6B:A1:51:2F:8A:DD:66:CE:D7:38:33:12:07:B5:46` |
   | `yubico-ca-1.pem` | Yubico Attestation Root 1 | `62:76:0C:6A:6E:F9:16:79:F4:54:C8:90:2B:80:FD:00:98:25:B3:F2:5D:A9:0F:1F:BA:CE:2E:C6:58:6C:D5:A8` |
   | `yubico-fido-ca-1.pem` | Yubico U2F Root CA Serial 457200631 | `0F:A1:38:6F:80:EB:87:13:26:3A:E5:C1:D8:4D:EB:45:5B:DF:08:AE:A5:0A:B0:55:03:CE:FE:E8:2B:09:2D:42` |
   | `yubico-fido-ca-2.pem` | Yubico FIDO Root CA Serial 450203556 | `35:F1:A5:4B:35:3B:FB:71:1E:6D:42:AD:BE:B7:6C:0E:9D:EA:D0:95:01:8E:6A:94:78:3B:A2:19:2F:D6:FA:AD` |

   Dann beide PIV-Wurzeln und die Zwischenzertifikate in eine Datei legen. PSAgent liest alle
   Zertifikate aus der Datei; die Zwischenzertifikate gelten dort wie Wurzeln, das ist gewollt:

   ```bash
   cat yubico-piv-ca-1.pem yubico-ca-1.pem yubico-intermediate.pem > yubico-piv-ca.pem
   sudo install -m 644 yubico-piv-ca.pem /etc/psagent/certs/yubico-piv-ca.pem
   ```

   Pfad in `PSAGENT_YUBICO_CA_PATH` eintragen. Den Ordner `/tmp/yubico` erst nach Abschnitt 3
   löschen, dort werden dieselben Dateien gebraucht.
2. Die Seriennummern der zugelassenen YubiKeys (`ykman list --serials`) durch Komma getrennt in
   `PSAGENT_YUBIKEY_SERIALS` eintragen. Ein YubiKey, der hier fehlt, signiert nicht mehr, auch
   wenn sein Zertifikat in der Liste steht. Läuft die App schon, danach die Container neu
   erstellen (`server-einrichten.md`, Schritt 5.2).

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

1. Die Wurzeln der FIDO-Bescheinigung aus den in Abschnitt 1 geladenen und geprüften Dateien
   zusammenstellen. Welche Wurzel ein YubiKey braucht, hängt von der Firmware ab. Laut Yubico
   kann man alle eintragen. Dazu kommen die Zwischenzertifikate:

   ```bash
   cd /tmp/yubico
   cat yubico-fido-ca-1.pem yubico-fido-ca-2.pem yubico-ca-1.pem yubico-intermediate.pem \
       > yubico-fido-ca.pem
   sudo install -m 644 yubico-fido-ca.pem /etc/psagent/certs/yubico-fido-ca.pem
   ```

   Pfad in `PSAGENT_YUBICO_FIDO_CA_PATH` eintragen.
2. Unter „Konto“ den YubiKey als Passkey einrichten. Der Browser fragt, ob die Website Marke
   und Modell des Schlüssels sehen darf: zulassen. Ein schon vorher eingerichteter Passkey zählt
   nicht; dann denselben YubiKey noch einmal einrichten (z. B. „YubiKey 1 (bescheinigt)“).
3. Den ersten YubiKey auf dem Server freigeben. Erst dann bestätigt er Änderungen; so kann
   niemand mit einer übernommenen Sitzung einen eigenen YubiKey unterschieben:

   ```bash
   sudo /opt/psagent/v2/deploy/manage.sh yubikey_freigeben <Benutzername>   # zeigt die Kandidaten
   sudo /opt/psagent/v2/deploy/manage.sh yubikey_freigeben <Benutzername> --passkey <Nummer>
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
`PSAGENT_YUBIKEY_SERIALS` streichen und die Container neu erstellen (`server-einrichten.md`,
Schritt 5.2); sonst gilt die Seriennummer in der laufenden App weiter.
Ein widerrufener Schlüssel lässt sich nicht wieder aufnehmen, auch nicht mit neuem Zertifikat.

## 6. Abgleichen

- Täglich: Die Zeugen-Mail nennt alle Fingerabdrücke. Der Vorstand vergleicht mit der
  Papierliste; ein unbekannter Fingerabdruck ist ein Sicherheitsvorfall (`vorfall-kette.md`).
- Jederzeit: `sudo /opt/psagent/v2/deploy/manage.sh kette_pruefen` meldet Zeilen der Liste, die
  an der App vorbei geändert, gelöscht oder eingefügt wurden. Mit `--fingerabdruecke <Datei>`
  vergleicht er die Liste in PSAgent mit der abgetippten Papierliste
  (`docs/runbooks/pruefwerkzeug.md`).
