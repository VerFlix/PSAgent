"""Statische Dateien im Betrieb (#22): WhiteNoise statt Caddy.

Festlegung Felix (24.10.2026): HTTPS macht `tailscale serve`, ohne Caddy. Darum liefert die App
CSS und JavaScript selbst aus (WhiteNoise), komprimiert und mit Prüfsumme im Dateinamen. Die
Dateien sammelt der Image-Bau mit `psagent.settings.collect` ein, ohne Geheimnisse.
"""

import importlib
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.test import override_settings

from psagent.settings import base


def test_whitenoise_comes_right_after_the_security_middleware() -> None:
    middleware = settings.MIDDLEWARE

    assert middleware[0] == "django.middleware.security.SecurityMiddleware"
    assert middleware[1] == "whitenoise.middleware.WhiteNoiseMiddleware"


def test_build_and_operation_use_the_same_storage() -> None:
    collect = importlib.import_module("psagent.settings.collect")

    assert collect.STORAGES == base.WHITENOISE_STORAGES
    assert (
        base.WHITENOISE_STORAGES["staticfiles"]["BACKEND"]
        == "whitenoise.storage.CompressedManifestStaticFilesStorage"
    )
    prod_source = (Path(base.__file__).parent / "prod.py").read_text()
    assert "STORAGES = WHITENOISE_STORAGES" in prod_source


def test_collectstatic_builds_a_manifest_with_compressed_files(tmp_path: Path) -> None:
    """Wie im Image-Bau: Manifest, Dateinamen mit Prüfsumme und gzip-Fassung daneben."""
    with override_settings(STORAGES=base.WHITENOISE_STORAGES, STATIC_ROOT=tmp_path):
        call_command("collectstatic", interactive=False, verbosity=0)

    assert (tmp_path / "staticfiles.json").exists()
    hashed = [path for path in tmp_path.rglob("htmx-2.0.4.min.*.js")]
    assert hashed, "htmx fehlt im Ergebnis"
    assert Path(f"{hashed[0]}.gz").exists()
