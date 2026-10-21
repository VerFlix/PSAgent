"""Hilfen für die Tests von Benutzern, Rollen und Sachkunde. Nur künstliche Testdaten."""

import itertools
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from accounts.models import Qualification, Role, User
from tests.accounts.softkey import FakeFidoRoot
from tests.accounts.yubikey import FakeYubico, pem

BERLIN = ZoneInfo("Europe/Berlin")
# Fester Zeitpunkt, damit Tests nicht vom heutigen Datum abhängen.
NOW = datetime(2026, 10, 15, 12, 0, tzinfo=BERLIN)
TODAY = NOW.date()

_numbers = itertools.count(1)

MakeUser = Callable[..., User]
Grant = Callable[..., Qualification]


@pytest.fixture
def make_user(db: None) -> MakeUser:
    """Legt ein Konto mit künstlichem Namen an."""

    def make(role: Role = Role.VIEWER, **fields: Any) -> User:
        number = next(_numbers)
        return User.objects.create_user(
            username=f"test{number}",
            first_name="Testperson",
            last_name=f"Beispiel {number}",
            role=role,
            **fields,
        )

    return make


@pytest.fixture
def grant(db: None) -> Grant:
    """Trägt eine Sachkunde ein."""

    def grant_qualification(user: User, valid_from: date, valid_until: date) -> Qualification:
        return Qualification.objects.create(
            user=user,
            valid_from=valid_from,
            valid_until=valid_until,
            evidence="Lehrgang PSA-Sachkunde (Testdaten)",
        )

    return grant_qualification


# Seriennummern der künstlichen YubiKeys, die die Einstellungen in Tests zulassen.
SERIALS = (1000001, 1000002)


@pytest.fixture
def yubico(settings: Any, tmp_path: Path) -> FakeYubico:
    """Künstliche Yubico-Wurzel als Vertrauensanker; zugelassen sind die Nummern aus SERIALS."""
    authority = FakeYubico()
    path = tmp_path / "yubico-ca.pem"
    path.write_bytes(pem(authority.root))
    settings.PSAGENT_YUBICO_CA_PATH = str(path)
    settings.PSAGENT_YUBIKEY_SERIALS = [str(serial) for serial in SERIALS]
    return authority


@pytest.fixture
def fido_root(settings: Any, tmp_path: Path) -> FakeFidoRoot:
    """Künstliche Wurzel der FIDO-Bescheinigung von Yubico, als Einstellung eingetragen (#95)."""
    authority = FakeFidoRoot()
    path = tmp_path / "yubico-fido-ca.pem"
    path.write_bytes(authority.pem())
    settings.PSAGENT_YUBICO_FIDO_CA_PATH = str(path)
    return authority
