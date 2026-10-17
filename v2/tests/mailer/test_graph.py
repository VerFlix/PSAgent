"""Versand über Microsoft Graph (#72): ein Postfach, keine Kopie in „Gesendete Elemente“.

Weder Microsoft noch ein Schlüssel wird hier angesprochen: Anmeldung (msal) und HTTP-Aufruf sind
ersetzt. Geprüft wird, was an Graph ginge.
"""

import json
from collections.abc import Iterator
from email.message import Message
from io import BytesIO
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.core.mail import EmailMessage, EmailMultiAlternatives
from django.test import override_settings

from mailer import graph

CONFIG = {
    "PSAGENT_GRAPH_TENANT_ID": "00000000-0000-0000-0000-000000000001",
    "PSAGENT_GRAPH_CLIENT_ID": "00000000-0000-0000-0000-000000000002",
    "PSAGENT_GRAPH_CERT_PATH": "",  # im Test gesetzt
    "PSAGENT_GRAPH_CERT_THUMBPRINT": "A" * 40,
    "DEFAULT_FROM_EMAIL": "psagent@example.org",
    "PSAGENT_MAIL_REPLY_TO": ["material@example.org"],
}


class FakeApp:
    def __init__(self, result: dict[str, Any]) -> None:
        self.result = result
        self.calls = 0

    def acquire_token_for_client(self, scopes: list[str]) -> dict[str, Any]:
        self.calls += 1
        assert scopes == ["https://graph.microsoft.com/.default"]
        return self.result


class FakeResponse:
    status = 202

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> Iterator[list[tuple[Request, float]]]:
    key = tmp_path / "graph.pem"
    key.write_text("Platzhalter statt Schlüssel")
    requests: list[tuple[Request, float]] = []
    app = FakeApp({"access_token": "token-kuenstlich"})
    monkeypatch.setattr(graph, "_application", lambda: app)

    def urlopen(request: Request, timeout: float) -> FakeResponse:
        requests.append((request, timeout))
        return FakeResponse()

    monkeypatch.setattr(graph, "urlopen", urlopen)
    with override_settings(**{**CONFIG, "PSAGENT_GRAPH_CERT_PATH": str(key)}):
        yield requests


def body(request: Request) -> dict[str, Any]:
    assert isinstance(request.data, bytes)
    result: dict[str, Any] = json.loads(request.data)
    return result


def test_sends_via_the_configured_mailbox(sent: list[tuple[Request, float]]) -> None:
    message = EmailMessage(
        "Quittung", "Hallo", from_email="jemand@example.org", to=["kim@example.org"]
    )

    assert graph.GraphEmailBackend().send_messages([message]) == 1

    [(request, timeout)] = sent
    assert request.full_url == (
        "https://graph.microsoft.com/v1.0/users/psagent%40example.org/sendMail"
    )
    assert request.get_method() == "POST" and timeout == graph.TIMEOUT
    assert request.get_header("Authorization") == "Bearer token-kuenstlich"
    payload = body(request)
    assert payload["saveToSentItems"] is False
    assert payload["message"] == {
        "subject": "Quittung",
        "body": {"contentType": "Text", "content": "Hallo"},
        "toRecipients": [{"emailAddress": {"address": "kim@example.org"}}],
        "ccRecipients": [],
        "bccRecipients": [],
        "replyTo": [{"emailAddress": {"address": "material@example.org"}}],
    }


def test_message_reply_to_wins(sent: list[tuple[Request, float]]) -> None:
    message = EmailMessage("x", "y", to=["kim@example.org"], reply_to=["a@example.org"])

    graph.GraphEmailBackend().send_messages([message])

    assert body(sent[0][0])["message"]["replyTo"] == [
        {"emailAddress": {"address": "a@example.org"}}
    ]


def test_attachments_and_html_are_refused(sent: list[tuple[Request, float]]) -> None:
    """Nur Text: Quittungen brauchen weder Anhänge noch HTML."""
    with_file = EmailMessage("x", "y", to=["kim@example.org"])
    with_file.attach("a.txt", "inhalt", "text/plain")
    with_html = EmailMultiAlternatives("x", "y", to=["kim@example.org"])
    with_html.attach_alternative("<p>y</p>", "text/html")

    for message in (with_file, with_html):
        with pytest.raises(graph.GraphError, match="nur Text"):
            graph.GraphEmailBackend().send_messages([message])

    assert sent == []


def test_failed_login_is_reported_without_details(monkeypatch: pytest.MonkeyPatch) -> None:
    app = FakeApp({"error": "invalid_client", "error_description": "AADSTS7000215 geheim"})
    monkeypatch.setattr(graph, "_application", lambda: app)

    with override_settings(**CONFIG), pytest.raises(graph.GraphError) as error:
        graph.GraphEmailBackend().send_messages([EmailMessage("x", "y", to=["k@example.org"])])

    assert "invalid_client" in str(error.value) and "geheim" not in str(error.value)


def test_http_error_names_status_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(graph, "_application", lambda: FakeApp({"access_token": "t"}))

    def fail(request: Request, timeout: float) -> None:
        raise HTTPError(request.full_url, 403, "Forbidden", Message(), BytesIO(b'{"x": "geheim"}'))

    monkeypatch.setattr(graph, "urlopen", fail)

    with override_settings(**CONFIG), pytest.raises(graph.GraphError, match="403") as error:
        graph.GraphEmailBackend().send_messages([EmailMessage("x", "y", to=["k@example.org"])])

    assert "geheim" not in str(error.value)


@pytest.mark.parametrize(
    "missing",
    [
        "PSAGENT_GRAPH_TENANT_ID",
        "PSAGENT_GRAPH_CLIENT_ID",
        "PSAGENT_GRAPH_CERT_PATH",
        "PSAGENT_GRAPH_CERT_THUMBPRINT",
    ],
)
def test_missing_configuration_is_named(missing: str) -> None:
    graph._application.cache_clear()
    complete = {**CONFIG, "PSAGENT_GRAPH_CERT_PATH": "/nicht/vorhanden.pem"}
    with (
        override_settings(**{**complete, missing: ""}),
        pytest.raises(ImproperlyConfigured, match=missing),
    ):
        graph._application()


def test_application_reads_key_file(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    key = tmp_path / "graph.pem"
    key.write_text("PEM-kuenstlich")
    seen: dict[str, Any] = {}

    class App:
        def __init__(self, client_id: str, **kwargs: Any) -> None:
            seen.update(client_id=client_id, **kwargs)

    monkeypatch.setattr("mailer.graph.msal.ConfidentialClientApplication", App)
    graph._application.cache_clear()
    with override_settings(**{**CONFIG, "PSAGENT_GRAPH_CERT_PATH": str(key)}):
        graph._application()
    graph._application.cache_clear()

    assert seen["client_id"] == CONFIG["PSAGENT_GRAPH_CLIENT_ID"]
    assert (
        seen["authority"]
        == f"https://login.microsoftonline.com/{CONFIG['PSAGENT_GRAPH_TENANT_ID']}"
    )
    assert seen["client_credential"] == {"private_key": "PEM-kuenstlich", "thumbprint": "A" * 40}
