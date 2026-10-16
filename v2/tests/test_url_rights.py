"""Jede URL trägt ein Recht oder ist ausdrücklich öffentlich; Prüfung im Server (#12).

Neue Ansichten fallen hier auf, wenn sie weder `requires(...)` noch `public` tragen. Für jede
geschützte URL gilt: ohne Anmeldung zur Anmeldung (htmx: 401 mit `HX-Redirect`), mit zu
niedriger Rolle 403, und eine abgewiesene Anfrage ändert nichts in der Datenbank.
"""

from collections.abc import Iterator
from typing import Any

import pytest
from django.apps import apps
from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.shortcuts import resolve_url
from django.test import Client
from django.urls import URLPattern, URLResolver, get_resolver, path, reverse
from django.urls.converters import IntConverter

from accounts.models import Role
from accounts.rights import Right, may_use, public, requires
from tests.helpers import make_inspector

LOGIN = resolve_url(settings.LOGIN_URL)
ROLES = (Role.ADMIN, Role.PPE_EXPERT, Role.TRAINER, Role.LENDER, Role.VIEWER)
# Bewusst öffentlich (docs/rechte.md, „Wo geprüft wird“).
PUBLIC = {
    "accounts:login",
    "accounts:passkey_login_options",
    "accounts:passkey_login",
    "accounts:logout",
    "accounts:enroll",
    "accounts:enroll_options",
    "accounts:enroll_finish",
    "healthz",
}


def walk(
    patterns: list[URLPattern | URLResolver], namespace: str = ""
) -> Iterator[tuple[str, URLPattern]]:
    for entry in patterns:
        if isinstance(entry, URLResolver):
            inner = f"{namespace}{entry.namespace}:" if entry.namespace else namespace
            yield from walk(entry.url_patterns, inner)
        else:
            yield f"{namespace}{entry.name}", entry


def unmarked(patterns: list[URLPattern | URLResolver]) -> list[str]:
    return [
        name
        for name, entry in walk(patterns)
        if not getattr(entry.callback, "psagent_right", None)
        and not getattr(entry.callback, "psagent_public", False)
    ]


def url_of(name: str, entry: URLPattern) -> str:
    converters = getattr(entry.pattern, "converters", {})
    kwargs: dict[str, Any] = {
        key: 999999 if isinstance(converter, IntConverter) else "unbekannt"
        for key, converter in converters.items()
    }
    return reverse(name, kwargs=kwargs)


ALL = list(walk(get_resolver().url_patterns))
PROTECTED = [
    (name, url_of(name, entry), entry.callback.psagent_right)  # type: ignore[attr-defined]
    for name, entry in ALL
    if getattr(entry.callback, "psagent_right", None)
]


def row_counts() -> dict[str, int]:
    return {model._meta.label: model._default_manager.count() for model in apps.get_models()}


def test_every_url_is_marked() -> None:
    assert unmarked(get_resolver().url_patterns) == []


def test_check_finds_unmarked_view() -> None:
    """Gegenprobe: Eine Ansicht ohne Kennzeichen fällt auf."""

    def view() -> Any:
        def plain(request: HttpRequest) -> HttpResponse:
            return HttpResponse()

        return plain

    patterns: list[URLPattern | URLResolver] = [
        path("ohne/", view(), name="ohne"),
        path("offen/", public(view()), name="offen"),
        path("geschuetzt/", requires(Right.ACCOUNT)(view()), name="geschuetzt"),
    ]

    assert unmarked(patterns) == ["ohne"]


def test_public_urls_are_exactly_the_intended_ones() -> None:
    marked_public = {name for name, entry in ALL if getattr(entry.callback, "psagent_public", 0)}

    assert marked_public == PUBLIC


@pytest.mark.django_db
@pytest.mark.parametrize("method", ["get", "post"])
@pytest.mark.parametrize(("name", "url", "right"), PROTECTED)
def test_anonymous_is_sent_to_login(
    name: str, url: str, right: Right, method: str, client: Client
) -> None:
    before = row_counts()

    response = getattr(client, method)(url)

    assert response.status_code == 302
    assert response["Location"].startswith(LOGIN)
    assert row_counts() == before


@pytest.mark.django_db
@pytest.mark.parametrize(("name", "url", "right"), PROTECTED)
def test_anonymous_htmx_request_is_redirected(
    name: str, url: str, right: Right, client: Client
) -> None:
    """htmx-Teilanfragen: kein HTML-Schnipsel der Anmeldeseite, sondern Neuladen der Seite."""
    response = client.post(url, headers={"HX-Request": "true"})

    assert response.status_code == 401
    assert response["HX-Redirect"] == LOGIN
    assert not response.content


@pytest.mark.django_db
@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize(("name", "url", "right"), PROTECTED)
def test_role_without_right_gets_403_and_nothing_changes(
    name: str, url: str, right: Right, role: Role, client: Client
) -> None:
    user = make_inspector(role=role)
    client.force_login(user)
    allowed = may_use(user, right)
    before = row_counts()

    responses = [
        client.get(url),
        client.post(url, {"name": "Tablet"}),
        client.post(url, headers={"HX-Request": "true"}),
    ]

    if allowed:
        assert all(response.status_code != 403 for response in responses)
    else:
        assert [response.status_code for response in responses] == [403, 403, 403]
        assert row_counts() == before


@pytest.mark.django_db
def test_403_page_names_the_missing_right(client: Client) -> None:
    client.force_login(make_inspector(role=Role.TRAINER))

    response = client.post(reverse("accounts:club_device_register"), {"name": "Tablet"})

    assert response.status_code == 403
    assert "erst ab der Rolle Admin" in response.content.decode()


@pytest.mark.django_db
def test_deactivated_account_loses_running_session(client: Client) -> None:
    user = make_inspector(role=Role.ADMIN)
    client.force_login(user)
    user.is_active = False
    user.save()

    response = client.get(reverse("home"))

    assert response.status_code == 302
    assert response["Location"].startswith(LOGIN)
