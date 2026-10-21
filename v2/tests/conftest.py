"""Gemeinsame Fixtures für alle Tests."""

import pytest

from tests.yubikey_client import YubiKeyClient


@pytest.fixture
def client() -> YubiKeyClient:
    """Wie die Fixture von pytest-django, kann aber Änderungen mit YubiKey bestätigen (#95)."""
    return YubiKeyClient()
