"""Hilfen für die Tests des Notbetriebs: dieselben künstlichen Daten wie beim Verleih."""

from pathlib import Path

import pytest
from django.test import override_settings

from tests.lending.conftest import borrower, fixed_today, lender, make_part

__all__ = ["borrower", "fixed_today", "lender", "make_part"]


@pytest.fixture(autouse=True)
def media(tmp_path: Path) -> object:
    with override_settings(MEDIA_ROOT=tmp_path / "media"):
        yield
