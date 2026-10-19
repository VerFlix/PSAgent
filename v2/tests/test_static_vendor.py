"""Lokal ausgelieferte Fremdbibliotheken sind unverändert (siehe static/vendor/README.md)."""

import hashlib
from pathlib import Path

import pytest

VENDOR = Path(__file__).resolve().parents[1] / "static" / "vendor"
EXPECTED = {
    "htmx-2.0.4.min.js": "e209dda5c8235479f3166defc7750e1dbcd5a5c1808b7792fc2e6733768fb447",
    "jsQR-1.4.0.js": "bc40c8a15196236b2314db0856f72ca0b49980cd5413b8c852a7349f5fee0859",
}


@pytest.mark.parametrize(("name", "digest"), EXPECTED.items())
def test_vendor_file_is_unchanged(name: str, digest: str) -> None:
    assert hashlib.sha256((VENDOR / name).read_bytes()).hexdigest() == digest


def test_every_vendor_file_is_listed() -> None:
    files = {path.name for path in VENDOR.iterdir() if path.suffix == ".js"}

    assert files == set(EXPECTED)
