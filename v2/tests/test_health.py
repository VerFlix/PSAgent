"""Tests für das Lebenszeichen /healthz."""

import pytest
from django.test import Client


@pytest.mark.django_db
def test_healthz_reports_ok_when_database_answers(client: Client) -> None:
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


@pytest.mark.django_db
def test_healthz_only_allows_get(client: Client) -> None:
    response = client.post("/healthz")

    assert response.status_code == 405


@pytest.mark.django_db
def test_healthz_sends_security_headers(client: Client) -> None:
    response = client.get("/healthz")

    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert "default-src 'self'" in response.headers["Content-Security-Policy"]
