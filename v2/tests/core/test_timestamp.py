"""Zeitstempel nach RFC 3161 (#16): gültige Stempel werden akzeptiert, alles andere abgelehnt."""

import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from asn1crypto import cms
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings

from core import timestamp
from core.timestamp import TimestampError
from tests.core.conftest import Authority, make_authority

DIGEST = hashlib.sha256(b"Kettenkopf, kuenstlich").digest()


def test_valid_timestamp_is_accepted(tsa: Authority) -> None:
    token = timestamp.request(DIGEST)

    stamped = timestamp.check(token, DIGEST)
    assert abs(stamped - datetime.now(UTC)) < timedelta(minutes=1)


def test_timestamp_for_other_data_is_refused(tsa: Authority) -> None:
    """Verbotener Fall: ein echter Stempel, aber für einen anderen Kettenkopf."""
    token = timestamp.request(DIGEST)

    with pytest.raises(TimestampError, match="gehört nicht zu diesem Hash"):
        timestamp.check(token, hashlib.sha256(b"anderer Kopf").digest())


def test_tampered_timestamp_is_refused(tsa: Authority) -> None:
    """Verbotener Fall: Das Token wurde verändert (Zeit oder Signatur)."""
    token = bytearray(timestamp.request(DIGEST))
    content = cms.ContentInfo.load(bytes(token))
    signature = content["content"]["signer_infos"][0]["signature"].native
    start = bytes(token).find(signature)
    token[start + 5] ^= 0xFF

    with pytest.raises(TimestampError):
        timestamp.check(bytes(token), DIGEST)


def test_timestamp_from_other_authority_is_refused(tsa: Authority) -> None:
    """Verbotener Fall: Ein selbst gebauter Dienst kann keine gültigen Stempel liefern."""
    forged = make_authority("Fremd").stamper
    import asyncio

    token = asyncio.run(forged.async_timestamp(DIGEST, "sha256")).dump()

    with pytest.raises(TimestampError, match="nicht vom konfigurierten Dienst"):
        timestamp.check(token, DIGEST)


def test_garbage_is_refused(tsa: Authority) -> None:
    with pytest.raises(TimestampError, match="unlesbar"):
        timestamp.check(b"kein Zeitstempel", DIGEST)


def test_unreachable_service_is_reported(monkeypatch: pytest.MonkeyPatch, tsa: Authority) -> None:
    class Down:
        async def async_timestamp(self, digest: bytes, algorithm: str) -> None:
            raise OSError("Verbindung abgelehnt")

    monkeypatch.setattr("core.timestamp.stamper", lambda: Down())

    with pytest.raises(TimestampError, match="nicht erreichbar"):
        timestamp.request(DIGEST)


@pytest.mark.parametrize("missing", ["PSAGENT_TSA_URL", "PSAGENT_TSA_CA_PATH"])
def test_missing_configuration_is_named(missing: str) -> None:
    with (
        override_settings(PSAGENT_TSA_URL="https://tsa.example.org", PSAGENT_TSA_CA_PATH="/x.pem"),
        override_settings(**{missing: ""}),
        pytest.raises(ImproperlyConfigured, match=missing),
    ):
        if missing == "PSAGENT_TSA_URL":
            timestamp.stamper()
        else:
            timestamp.trust_roots()
