# PSAgent 2.0

Web-App für Materialverleih und PSA-Prüfungen im Verein. Neubau des Altsystems im
Repo-Wurzelverzeichnis (`psa_gui.py`, `verleih_gui.py`), das bis zum Start von Stufe 1
weiterläuft.

- Konzept: „PSAgent 2.0 – Konzept“ (claude.ai-Dokument, Link im Claude-Projekt „PSAgent“)
- Regeln für die Arbeit am Code: [`../CLAUDE.md`](../CLAUDE.md)
- Abnahmekatalog: [`docs/paritaet.md`](docs/paritaet.md)
- Altsystem-Schema und Testdaten: [`docs/altsystem-testdaten.md`](docs/altsystem-testdaten.md)
- Import der Altdaten: [`docs/altsystem-import.md`](docs/altsystem-import.md)

## Lokal starten

Voraussetzungen: [uv](https://docs.astral.sh/uv/) und Docker.

```bash
cd v2
docker compose up -d db              # PostgreSQL auf 127.0.0.1:5432
uv sync                              # Python 3.13 und Abhängigkeiten
uv run python manage.py migrate
uv run python manage.py createsuperuser   # erstes Konto, bekommt die Rolle Admin
uv run python manage.py runserver    # http://127.0.0.1:8000/healthz
```

Ohne `.env` gelten die Standardwerte aus `psagent/settings/dev.py`. Eigene Werte kommen in
`.env` (Vorlage: `.env.example`) und werden mit `uv run --env-file .env …` geladen.

## Prüfen

```bash
uv run pytest                        # Tests (braucht die Datenbank aus docker compose)
uv run ruff check . && uv run ruff format --check .
uv run mypy .
```

Einmalig im Repo-Wurzelverzeichnis: `uv run --project v2 pre-commit install`. Danach laufen
Formatierung, Linter und der Schutz gegen eingecheckte Schlüssel vor jedem Commit.

Dieselben Prüfungen laufen in GitHub Actions für jeden Pull Request nach `v2`, dazu ein
Secret-Scan mit gitleaks und ein Docker-Build für amd64 und arm64 (Pi).

## Anmeldung

- Passkey (FIDO2/WebAuthn, z. B. YubiKey) für alle Rollen; Admin und PSA-Sachkundige melden
  sich **nur** mit Passkey an. Trainer/Materialwart, Verleiher und Viewer dürfen zusätzlich
  ein Passwort nutzen. Nach 5 Fehlversuchen ist die Kombination aus Benutzername und
  Adresse 15 Minuten gesperrt.
- Konten legt ein Admin unter „Nutzer verwalten“ an (`/verwaltung/nutzer/`): Rolle,
  Aktiv-Status, Sachkunde und Passkeys der anderen, jeweils mit Begründung und Protokoll.
  Zugang gibt es nur über einen Einmal-Link (72 Stunden gültig): Admin und PSA-Sachkundige
  richten damit einen Passkey ein, die übrigen Rollen wahlweise einen Passkey oder ein eigenes
  Passwort. Ein neuer Link macht ältere ungültig.
- Notzugang auf dem Server (z. B. für das erste Admin-Konto nach `createsuperuser`), 24 Stunden
  gültig, ebenfalls protokolliert:

  ```bash
  uv run python manage.py passkey_einrichtung <benutzername>
  ```

- Weitere Passkeys verwaltet jede Person unter „Konto“. Der letzte Passkey eines Admins oder
  Sachkundigen lässt sich nicht widerrufen.
- Vereinsgerät (z. B. Tablet an der Ausgabe): Ein Admin legt es unter „Konto“ fest. Dort endet
  die Sitzung nach 3 Minuten ohne Eingabe und nach jedem abgeschlossenen Vorgang; sonst gilt
  sie 8 Stunden.
- Einstellungen: `PSAGENT_WEBAUTHN_RP_ID`, `PSAGENT_WEBAUTHN_ORIGINS`, im Betrieb hinter Caddy
  `PSAGENT_TRUSTED_PROXIES`, der Verein als Besitzer auf der Einsatzdokumentation
  `PSAGENT_CLUB_NAME` (siehe `.env.example`).
- Notbetrieb (`/notbetrieb/`, ab Verleiher): Notfallmappe als PDF mit allen Teilen, anstehenden
  Ausleihen (Entleiher nur mit Namen) und leeren Formularen mit fortlaufender Formularnummer.
  Für die wöchentliche Fassung im Betrieb einen Timer auf
  `uv run python manage.py notfallmappe` setzen. Gespeichert bleiben die letzten 5 Mappen.
  Nach dem Ausfall: Formulare mit Nummer, Scan (PDF oder Fotos) und Originaldatum nacherfassen
  (zuerst Sperrungen); fehlende Nummern und nicht übernehmbare Zeilen stehen auf der
  Klärungsliste für PSA-Sachkundige und Admin.
- PDF-Dokumente je Teil (Teileseite, Kasten „Dokumente“): Einsatzdokumentation ab Trainer;
  PD und Haftungsausschluss nur mit gültiger Sachkunde, mit Name und Briefkopf/Stempel aus
  dem Konto der erzeugenden Person.

Browser-Tests (Playwright, eigener CI-Job; bei Fehlern Screenshots und Traces als Artefakt):

```bash
uv run playwright install chromium   # einmalig lokal; in Cloud-Sitzungen vorinstalliert
uv run pytest -m e2e
```

Jeder Oberflächen-PR bringt seinen Browser-Test mit. Hilfen in `tests/e2e/conftest.py`:
`sign_in` meldet eine Person einer Rolle direkt an, `device_page` läuft als Desktop, Handy
(Pixel 7) und Tablet (iPad Mini), `assert_fits_width` prüft, dass nichts seitlich übersteht.

## Testdaten im Format des Altsystems

Echte Altdaten bleiben außen vor (CLAUDE.md, Regel 1). Künstliche Datenbanken in allen
Schema-Varianten des Altsystems erzeugt der Testdaten-Generator:

```bash
uv run python manage.py legacy_testdb --liste
uv run python manage.py legacy_testdb --variant s7 --seed 42 --out /tmp/altdaten
```

Details zu Varianten, Inhalt und Randfällen: [`docs/altsystem-testdaten.md`](docs/altsystem-testdaten.md).
Die Tests in `tests/legacy/` vergleichen jede Variante mit dem echten Altsystem-Code aus der
Git-Historie und brauchen deshalb einen Checkout mit voller Historie.

Import (nur in eine leere Datenbank; erst Probelauf, dann echt):

```bash
uv run python manage.py legacy_import /tmp/altdaten --probelauf --bericht /tmp/bericht.md
uv run python manage.py legacy_import /tmp/altdaten --bericht /tmp/bericht-import.md
```

## Cloud-Sitzungen (Claude Code im Web)

In Cloud-Sitzungen richtet ein SessionStart-Hook die Umgebung beim Start und beim Fortsetzen
ein (`.claude/hooks/session_start.sh`, eingetragen in `.claude/settings.json`):

1. `uv sync --locked` in `v2/`
2. Docker-Daemon starten, falls er nicht läuft
3. `docker compose up -d --wait db` und warten, bis PostgreSQL 17 auf `127.0.0.1:5432` antwortet

Danach laufen `uv run pytest`, `ruff` und `mypy` ohne weitere Schritte. Der Hook greift nur
bei `CLAUDE_CODE_REMOTE=true`; lokal tut er nichts. Er braucht Netzzugriff auf PyPI und
Docker Hub. Protokolle liegen unter `/tmp/psagent-session-start/`. Von Hand starten, im
Repo-Wurzelverzeichnis:

```bash
CLAUDE_CODE_REMOTE=true bash .claude/hooks/session_start.sh
```

## Arbeitsweise

- Branch `v2` sammelt den Neubau. Gearbeitet wird in `feature/<Issue-Nr>-<Kurzname>`,
  zusammengeführt per Pull Request nach `v2`.
- `master` bleibt beim Altsystem, bis Stufe 1 live geht.
- Kleine Pull Requests, Tests zuerst, je Pull Request ein Issue.
