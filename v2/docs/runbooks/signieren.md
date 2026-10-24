# Runbook: Prüfprotokolle am PC mit dem YubiKey signieren

Gilt ab #38 (Schritt 3). **Erproben mit echtem YubiKey: lokal (#21).** Android: #101 (später).

## Wozu

Eine Prüfungsrunde wirkt erst, wenn der Prüfer ihr Prüfprotokoll mit seinem YubiKey signiert und
in PSAgent hochgeladen hat. Das Signier-Tool `signieren` macht das am PC:

1. Es zeigt das Protokoll an (Runde, Anzahl der Teile, Kettenkopf) und öffnet das PDF.
2. Nach „j“ fragt es die PIN; der YubiKey blinkt und will berührt werden.
3. Es signiert in das vorbereitete Feld „Sachkundiger“ und legt
   `<name>_signiert.pdf` neben das Original. Das Feld zeigt die hinterlegte Unterschrift als
   Bild, Name, Datum und „Gültig ist die digitale Signatur“.

Das Tool braucht keine Zugangsdaten zu PSAgent. Hochgeladen wird im Browser.

## 1. Einrichten (einmalig, Windows)

1. **Yubico PIV Tool** installieren (developers.yubico.com, „Yubico PIV Tool“, Windows-Installer).
   Es bringt `libykcs11.dll` mit, standardmäßig unter
   `C:\Program Files\Yubico\Yubico PIV Tool\bin\`.
2. **uv** installieren: `winget install astral-sh.uv`
3. Das Repo holen (`git clone …`) und im Repo-Ordner das Tool installieren:

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\v2\signiertool\installieren.ps1
   ```

   Danach gibt es den Befehl `signieren` in jeder neuen Eingabeaufforderung. Das Skript
   installiert genau die Versionen aus `v2/signiertool/requirements.txt` (erzeugt aus
   `v2/uv.lock`, in CI mit geprüft) und prüft dabei jede Prüfsumme (#105). `uv tool install`
   nicht mehr verwenden: Es nähme die Versionen, prüfte die Prüfsummen aber nicht.
   **Noch nicht unter Windows erprobt** (lokal mit Felix).
4. **Schlüssel und Zertifikat** im YubiKey (Slot 9c) wie im Runbook `zertifikate.md` erzeugen,
   am besten mit `--algorithm ECCP256`. Das Zertifikat in PSAgent aufnehmen lassen (Admin).
5. **Unterschrift als Bild** einmal hinterlegen, z. B. ein Scan der Unterschrift auf weißem
   Papier, zugeschnitten (PNG oder JPG):

   ```powershell
   signieren unterschrift C:\Pfad\unterschrift.png
   ```

   Das Bild liegt danach unter `%APPDATA%\PSAgent\unterschrift.png`, nicht im Repo. Ohne Bild
   zeigt das Feld nur den Text.

Liegt `libykcs11.dll` woanders: `--modul <Pfad>` beim Signieren angeben oder die
Umgebungsvariable `PSAGENT_PKCS11_MODUL` setzen.

## 2. Je Prüfungsrunde

1. In PSAgent im Prüfmodus die Runde abschließen und **„Prüfprotokoll zum Signieren (PDF)“**
   herunterladen.
2. YubiKey anstecken (nur einen) und signieren:

   ```powershell
   signieren "$HOME\Downloads\Pruefprotokoll_Runde_12.pdf"
   ```

3. Angaben prüfen, das geöffnete PDF ansehen, mit `j` bestätigen, PIN eingeben, YubiKey
   berühren.
4. Im Prüfmodus bei derselben Runde **`Pruefprotokoll_Runde_12_signiert.pdf`** hochladen
   („Signatur prüfen und Runde wirksam machen“).

Der Server lehnt ab, wenn das PDF nicht genau das heruntergeladene ist, nach dem Signieren
verändert wurde, das Zertifikat nicht hinterlegt oder widerrufen ist oder die Sachkunde beim
Hochladen nicht gilt. Der Grund steht in der Meldung.

## 3. Wenn etwas nicht klappt

| Meldung | Abhilfe |
| --- | --- |
| PKCS#11-Modul nicht gefunden | Yubico PIV Tool installieren oder `--modul` angeben |
| Kein YubiKey gefunden | YubiKey anstecken; ggf. ab- und wieder anstecken |
| Mehrere YubiKeys gefunden | Nur einen YubiKey anstecken |
| YubiKey nicht erreichbar oder PIN falsch | PIN prüfen. Nach drei falschen Versuchen ist die PIN gesperrt; entsperren mit der PUK (`ykman piv access unblock-pin`) |
| Das PDF ist bereits signiert | Das Original nehmen, nicht die `_signiert`-Datei |
| `…_signiert.pdf` gibt es schon | Alte Datei umbenennen oder löschen |

## 4. Aktualisieren

Nach einem `git pull` dasselbe Skript noch einmal ausführen; es ersetzt die alte Installation:

```powershell
powershell -ExecutionPolicy Bypass -File .\v2\signiertool\installieren.ps1
```

## Signierte Berichte prüfen

`signieren pruefen <Bericht.pdf>` prüft einen signierten Prüfbericht ohne Server und ohne
YubiKey (#19): `docs/runbooks/pruefwerkzeug.md`, Abschnitt 5.

## Alternative: Adobe Acrobat Reader

Laut Konzept geht auch Acrobat Reader unter Windows (YubiKey über den Yubico Minidriver). Es muss
in das vorhandene Feld „Sachkundiger“ signiert werden und darf sonst nichts ändern, sonst lehnt
der Server ab. **Noch nicht erprobt** (#21).
