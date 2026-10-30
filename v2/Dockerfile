# syntax=docker/dockerfile:1
# Image für den Betrieb (amd64 auf dem Server, arm64 für einen Pi 5 als Ersatzgerät).
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

COPY --from=ghcr.io/astral-sh/uv:0.13.1 /uv /uvx /bin/

WORKDIR /app

# Abhängigkeiten zuerst, damit der Docker-Cache bei Code-Änderungen greift.
# Solange noch kein uv.lock existiert, wird frisch aufgelöst.
COPY pyproject.toml uv.lock* ./
# Das Signier-Tool (Entwicklungsabhängigkeit, #38) wird nicht installiert; uv braucht nur seine
# Paketbeschreibung, um uv.lock zu prüfen.
COPY signiertool/pyproject.toml signiertool/
RUN if [ -f uv.lock ]; then uv sync --locked --no-dev; else uv sync --no-dev; fi

COPY . .
# Statische Dateien einsammeln (WhiteNoise, #22); ohne Geheimnisse und ohne Datenbank.
RUN DJANGO_SETTINGS_MODULE=psagent.settings.collect .venv/bin/python manage.py collectstatic --noinput

RUN useradd --system --uid 10001 --no-create-home psagent
USER psagent

ENV PATH="/app/.venv/bin:$PATH" \
    DJANGO_SETTINGS_MODULE=psagent.settings.prod

EXPOSE 8000
CMD ["gunicorn", "psagent.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "2", "--access-logfile", "-"]
