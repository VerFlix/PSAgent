"""Die Betriebseinstellungen müssen gehärtet sein und Pflichtwerte erzwingen."""

import importlib
import sys
from types import ModuleType

import pytest
from django.core.exceptions import ImproperlyConfigured

PROD = "psagent.settings.prod"


def load_prod_settings() -> ModuleType:
    """Lädt die Betriebseinstellungen frisch, ohne die aktiven Test-Einstellungen zu berühren."""
    sys.modules.pop(PROD, None)
    return importlib.import_module(PROD)


@pytest.fixture
def prod_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    monkeypatch.setenv("DJANGO_SECRET_KEY", "x" * 50)
    monkeypatch.setenv("DJANGO_ALLOWED_HOSTS", "psagent.example.ts.net")
    monkeypatch.setenv("DJANGO_CSRF_TRUSTED_ORIGINS", "https://psagent.example.ts.net")
    monkeypatch.setenv("POSTGRES_PASSWORD", "nur-test")
    return monkeypatch


def test_prod_settings_are_hardened(prod_env: pytest.MonkeyPatch) -> None:
    prod = load_prod_settings()

    assert prod.DEBUG is False
    assert prod.ALLOWED_HOSTS == ["psagent.example.ts.net"]
    assert prod.SESSION_COOKIE_SECURE is True
    assert prod.CSRF_COOKIE_SECURE is True
    assert prod.SECURE_SSL_REDIRECT is True
    assert prod.SECURE_HSTS_SECONDS >= 60 * 60 * 24 * 365
    assert prod.PASSWORD_HASHERS[0].endswith("Argon2PasswordHasher")


@pytest.mark.parametrize(
    "missing",
    ["DJANGO_SECRET_KEY", "POSTGRES_PASSWORD"],
)
def test_prod_settings_refuse_to_start_without_secrets(
    prod_env: pytest.MonkeyPatch, missing: str
) -> None:
    prod_env.delenv(missing)

    with pytest.raises(ImproperlyConfigured):
        load_prod_settings()
