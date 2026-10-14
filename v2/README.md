# PSAgent 2.0

Web-App für Materialverleih und PSA-Prüfungen im Verein. Neubau des Altsystems im
Repo-Wurzelverzeichnis (`psa_gui.py`, `verleih_gui.py`), das bis zum Start von Stufe 1
weiterläuft.

- Konzept: „PSAgent 2.0 – Konzept“ (claude.ai-Dokument, Link im Claude-Projekt „PSAgent“)
- Regeln für die Arbeit am Code: [`../CLAUDE.md`](../CLAUDE.md)
- Abnahmekatalog: [`docs/paritaet.md`](docs/paritaet.md)
- Altsystem-Schema und Testdaten: [`docs/altsystem-testdaten.md`](docs/altsystem-testdaten.md)

## Lokal starten

Voraussetzungen: [uv](https://docs.astral.sh/uv/) und Docker.

```bash
cd v2
docker compose up -d db              # PostgreSQL auf 127.0.0.1:5432
uv sync                              # Python 3.13 und Abhängigkeiten
uv run python manage.py migrate
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
