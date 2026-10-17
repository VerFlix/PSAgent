# Runbook: E-Mail über Microsoft 365 einrichten (#72)

**Lokal mit Felix.** Schlüssel erzeugt nur Felix; die Schlüsseldatei kommt nie ins Repo und nie
in eine Cloud-Sitzung. Im Text steht `<domain>` für die eigene Domain des Vereins.

## Warum Graph statt SMTP

Microsoft schaltet die Anmeldung mit Benutzername und Passwort für SMTP AUTH in Exchange Online
**Ende Dezember 2026** standardmäßig ab; das endgültige Aus folgt 2027. Ein SMTP-Relay über
einen Connector bräuchte eine feste IP-Adresse. PSAgent sendet deshalb über Microsoft Graph
(`sendMail`) mit einer App-Registrierung und einem Zertifikat. Das reicht mit der kostenlosen
Lizenz Business Basic für gemeinnützige Organisationen.

## 1. Freigegebenes Postfach

Im Exchange Admin Center unter Empfänger, Postfächer, ein freigegebenes Postfach hinzufügen.
Alternativ in Exchange Online PowerShell (`Connect-ExchangeOnline`):

```powershell
New-Mailbox -Shared -Name "PSAgent" -DisplayName "PSAgent" -PrimarySmtpAddress psagent@<domain>
```

Ein freigegebenes Postfach braucht keine Lizenz. Antworten der Entleiher gehen per Reply-To an
ein Funktionspostfach, z. B. `material@<domain>` (`PSAGENT_MAIL_REPLY_TO`).

## 2. Zertifikat erzeugen (nur Felix)

Auf dem eigenen Rechner oder direkt auf dem Pi:

```bash
openssl req -x509 -newkey rsa:3072 -nodes -days 730 \
  -subj "/CN=PSAgent Mailversand" -keyout graph.pem -out graph.crt
openssl x509 -in graph.crt -noout -fingerprint -sha1 | cut -d= -f2 | tr -d :
```

Die zweite Zeile gibt den Fingerabdruck aus (`PSAGENT_GRAPH_CERT_THUMBPRINT`).

- `graph.pem` (privater Schlüssel) auf den Pi, z. B. `/etc/psagent/graph.pem`, Eigentümer der
  Dienstnutzer, Rechte `600`.
- Nur `graph.crt` geht zu Microsoft.
- Ablauf nach 2 Jahren im Kalender eintragen; vorher ein neues Zertifikat hochladen.

## 3. App-Registrierung in Entra

1. Entra Admin Center, App-Registrierungen, „Neue Registrierung“: Name „PSAgent Mailversand“,
   nur dieses Verzeichnis, keine Umleitungs-URI.
2. Unter „Zertifikate & Geheimnisse“ `graph.crt` hochladen. Keinen geheimen Clientschlüssel
   anlegen.
3. Notieren: Anwendungs-ID (Client) und Verzeichnis-ID (Mandant).
4. **Keine** API-Berechtigung `Mail.Send` hinzufügen. In Entra gälte sie für alle Postfächer des
   Vereins.
5. Unter Unternehmensanwendungen, „PSAgent Mailversand“, die **Objekt-ID** notieren. Sie
   unterscheidet sich von der Objekt-ID auf der Seite der App-Registrierung.

## 4. Recht nur für das eine Postfach (RBAC for Applications)

In Exchange Online PowerShell:

```powershell
New-ServicePrincipal -AppId <Anwendungs-ID> -ObjectId <Objekt-ID der Unternehmensanwendung> `
  -DisplayName "PSAgent Mailversand"
New-ManagementScope -Name "PSAgent Postfach" `
  -RecipientRestrictionFilter "PrimarySmtpAddress -eq 'psagent@<domain>'"
New-ManagementRoleAssignment -App <Anwendungs-ID> -Role "Application Mail.Send" `
  -CustomResourceScope "PSAgent Postfach"
```

Prüfen; der Test umgeht den Zwischenspeicher, im Betrieb kann eine Änderung 30 Minuten bis 2
Stunden dauern:

```powershell
Test-ServicePrincipalAuthorization -Identity <Anwendungs-ID> -Resource psagent@<domain>
Test-ServicePrincipalAuthorization -Identity <Anwendungs-ID> -Resource <anderes Postfach>
```

Beim ersten Postfach muss `Application Mail.Send` mit `InScope` = `True` erscheinen, beim
zweiten `False`.

## 5. DNS

- **SPF:** `v=spf1 include:spf.protection.outlook.com -all`; vorhandene Einträge ergänzen,
  nicht doppeln.
- **DKIM:** im Microsoft Defender Portal (E-Mail-Authentifizierung) für `<domain>` einschalten;
  die beiden CNAME-Einträge beim DNS-Anbieter anlegen.
- **DMARC:** zuerst `v=DMARC1; p=none; rua=mailto:dmarc@<domain>`, nach einigen Wochen ohne
  Fehler auf `p=quarantine`.

## 6. PSAgent konfigurieren

Umgebungsvariablen auf dem Pi (Vorlage `.env.example`):

```
PSAGENT_MAIL_BACKEND=mailer.graph.GraphEmailBackend
PSAGENT_MAIL_FROM=psagent@<domain>
PSAGENT_MAIL_REPLY_TO=material@<domain>
PSAGENT_GRAPH_TENANT_ID=<Verzeichnis-ID>
PSAGENT_GRAPH_CLIENT_ID=<Anwendungs-ID>
PSAGENT_GRAPH_CERT_PATH=/etc/psagent/graph.pem
PSAGENT_GRAPH_CERT_THUMBPRINT=<Fingerabdruck>
```

Ausgehend braucht der Pi nur HTTPS zu `login.microsoftonline.com` und `graph.microsoft.com`.

## 7. Testversand

```bash
uv run python manage.py sendtestemail <eigene Adresse>
```

Danach prüfen:

- Die Mail ist angekommen, Absender `psagent@<domain>`, Antworten gehen an das Funktionspostfach.
- Im Kopf der Mail stehen `spf=pass`, `dkim=pass` und `dmarc=pass`.
- Unter „Gesendete Elemente“ von `psagent@<domain>` liegt **keine** Kopie; PSAgent sendet mit
  `saveToSentItems = false`.

## 8. Erinnerungen bei Überfälligkeit

Einen täglichen Timer auf `uv run python manage.py erinnerungen` setzen, z. B. um 9 Uhr. Zuerst
mit `--probelauf` prüfen. Die Ausgabe nennt nur Vorgangsnummern. Schlägt ein Versand fehl,
versucht es der nächste Lauf erneut. Ausschalten für alle: Admin auf der Seite „Anstehende
Ausleihen“.

## Fehlerbilder

| Meldung | Ursache |
| --- | --- |
| `Anmeldung bei Microsoft 365 fehlgeschlagen (invalid_client)` | Fingerabdruck oder Zertifikat passt nicht, oder es ist abgelaufen |
| `HTTP 403` | RBAC-Zuweisung fehlt, gilt noch nicht (Zwischenspeicher) oder der Absender ist ein anderes Postfach |
| `HTTP 404` | `PSAGENT_MAIL_FROM` gibt es nicht als Postfach |
| `… fehlt für den Versand über Microsoft 365` | Umgebungsvariable nicht gesetzt |
| `Microsoft 365 ist nicht erreichbar.` | Netz oder DNS auf dem Pi |
