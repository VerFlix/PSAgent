# Signier-Tool installieren oder aktualisieren (Windows, #105).
#
# Installiert genau die Versionen aus requirements.txt (erzeugt aus v2/uv.lock) und prüft dabei
# jede Prüfsumme (--require-hashes). `uv tool install` nähme zwar die Versionen, prüfte die
# Prüfsummen aber nicht. Das Tool selbst wird nicht gebaut, sondern als reiner Python-Code
# kopiert: So läuft kein Build-Werkzeug ohne Prüfsumme auf dem Signier-PC. Aufruf im Repo-Ordner:
#
#     powershell -ExecutionPolicy Bypass -File .\v2\signiertool\installieren.ps1
#
# Danach gibt es den Befehl `signieren` in jeder neuen Eingabeaufforderung.

$ErrorActionPreference = "Stop"

function Run([string[]] $Command) {
    & $Command[0] $Command[1..($Command.Length - 1)]
    if ($LASTEXITCODE -ne 0) { throw "Fehlgeschlagen: $($Command -join ' ')" }
}

$Source = $PSScriptRoot
$Requirements = Join-Path $Source "requirements.txt"
$Target = Join-Path $env:LOCALAPPDATA "PSAgent\signiertool"
$Python = Join-Path $Target "Scripts\python.exe"
$Bin = Join-Path $env:USERPROFILE ".local\bin"

# Eine frühere Installation mit `uv tool install` entfernen, sonst verdeckt sie den neuen Befehl.
& uv tool uninstall psagent-signieren 2>$null | Out-Null

if (Test-Path $Target) { Remove-Item -Recurse -Force $Target }
Run @("uv", "venv", "--python", "3.13", $Target)
Run @("uv", "pip", "install", "--python", $Target, "--require-hashes", "-r", $Requirements)

# Den Code des Tools in die Umgebung kopieren (nur .py-Dateien, kein Build).
$SitePackages = & $Python -c "import sysconfig; print(sysconfig.get_paths()['purelib'])"
if ($LASTEXITCODE -ne 0) { throw "Python der neuen Umgebung startet nicht." }
$Package = Join-Path $SitePackages "psagent_signieren"
New-Item -ItemType Directory -Force -Path $Package | Out-Null
Copy-Item -Path (Join-Path $Source "psagent_signieren\*.py") -Destination $Package

# Starter im Ordner, den uv ohnehin in den Suchpfad legt (`uv tool update-shell`).
New-Item -ItemType Directory -Force -Path $Bin | Out-Null
Set-Content -Path (Join-Path $Bin "signieren.cmd") `
    -Value "@`"$Python`" -m psagent_signieren.cli %*" -Encoding ASCII
Run @("uv", "tool", "update-shell")

Write-Host ""
Write-Host "Signier-Tool installiert: $Target"
Write-Host "Befehl: signieren (in einer neuen Eingabeaufforderung)"
& $Python -m psagent_signieren.cli --help | Select-Object -First 1
