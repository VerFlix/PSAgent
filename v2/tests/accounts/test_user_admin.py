"""Nutzer, Rollen und Sachkunde verwalten (#67): Dienste, Schutzregeln, Protokoll."""

from datetime import date, timedelta

import pytest
from django.core.exceptions import PermissionDenied
from django.utils import timezone

from accounts.enrollment import find_token
from accounts.models import (
    AccountEvent,
    AccountEventKind,
    EnrollmentToken,
    Passkey,
    Qualification,
    QualificationRevocation,
    Role,
    User,
)
from accounts.services import (
    AccountError,
    change_role,
    create_account,
    deactivate,
    grant_qualification,
    issue_enrollment,
    reactivate,
    revoke_passkey,
    revoke_qualification,
)
from core.append_only import AppendOnlyError
from tests.accounts.conftest import MakeUser

pytestmark = pytest.mark.django_db

OTHER_ROLES = [Role.PPE_EXPERT, Role.TRAINER, Role.LENDER, Role.VIEWER]


@pytest.fixture
def admin(make_user: MakeUser) -> User:
    return make_user(Role.ADMIN)


def events(user: User) -> list[tuple[str, str, str]]:
    return list(user.account_events.values_list("kind", "old_role", "new_role"))


# --- Konto anlegen und Einrichtungslink ---


def test_create_account_issues_enrollment_link(admin: User) -> None:
    user, token = create_account(
        "kim.beispiel", first_name="Kim", last_name="Beispiel", role=Role.LENDER, actor=admin
    )

    assert user.role == Role.LENDER
    assert not user.has_usable_password()
    entry = find_token(token)
    assert entry is not None and entry.user == user
    assert events(user) == [
        (AccountEventKind.CREATED, "", Role.LENDER),
        (AccountEventKind.ENROLLMENT_ISSUED, "", ""),
    ]
    assert {event.actor for event in user.account_events.all()} == {admin}


def test_create_account_rejects_duplicate_username(admin: User) -> None:
    create_account("kim", first_name="Kim", last_name="Beispiel", role=Role.LENDER, actor=admin)

    with pytest.raises(AccountError, match="vergeben"):
        create_account("KIM", first_name="Kim", last_name="Probe", role=Role.VIEWER, actor=admin)

    assert User.objects.filter(username__iexact="kim").count() == 1


@pytest.mark.parametrize("role", OTHER_ROLES)
def test_only_admin_creates_accounts(role: Role, make_user: MakeUser) -> None:
    """Verbotener Fall: andere Rollen legen keine Konten an; es entsteht nichts."""
    actor = make_user(role)
    before = User.objects.count()

    with pytest.raises(PermissionDenied, match="ab der Rolle Admin"):
        create_account("neu", first_name="Neu", last_name="Konto", role=Role.ADMIN, actor=actor)

    assert User.objects.count() == before
    assert not AccountEvent.objects.exists()


def test_new_enrollment_link_replaces_open_ones(admin: User, make_user: MakeUser) -> None:
    user = make_user(Role.LENDER)
    first = issue_enrollment(user, actor=admin)

    second = issue_enrollment(user, actor=admin)

    assert find_token(first) is None
    assert find_token(second) is not None
    assert EnrollmentToken.objects.filter(user=user).count() == 2


@pytest.mark.parametrize("role", OTHER_ROLES)
def test_only_admin_issues_enrollment_links(role: Role, make_user: MakeUser) -> None:
    user = make_user(Role.LENDER)

    with pytest.raises(PermissionDenied):
        issue_enrollment(user, actor=make_user(role))

    assert not EnrollmentToken.objects.exists()
    assert not AccountEvent.objects.exists()


def test_command_link_is_logged_without_actor(make_user: MakeUser) -> None:
    """Notzugang über den Verwaltungsbefehl: protokolliert, `durch` bleibt leer."""
    user = make_user(Role.ADMIN)

    issue_enrollment(user, actor=None)

    event = user.account_events.get()
    assert (event.kind, event.actor) == (AccountEventKind.ENROLLMENT_ISSUED, None)


# --- Rolle ändern ---


def test_change_role_is_logged(admin: User, make_user: MakeUser) -> None:
    user = make_user(Role.LENDER)

    change_role(user, Role.TRAINER, actor=admin, reason="Materialwart seit Oktober")

    user.refresh_from_db()
    assert user.role == Role.TRAINER
    event = user.account_events.get()
    assert (event.kind, event.old_role, event.new_role) == (
        AccountEventKind.ROLE_CHANGED,
        Role.LENDER,
        Role.TRAINER,
    )
    assert (event.actor, event.reason) == (admin, "Materialwart seit Oktober")


@pytest.mark.parametrize("reason", ["", "   "])
def test_change_role_needs_reason(reason: str, admin: User, make_user: MakeUser) -> None:
    user = make_user(Role.LENDER)

    with pytest.raises(AccountError, match="Begründung"):
        change_role(user, Role.TRAINER, actor=admin, reason=reason)

    user.refresh_from_db()
    assert user.role == Role.LENDER
    assert not AccountEvent.objects.exists()


def test_same_role_is_rejected(admin: User, make_user: MakeUser) -> None:
    user = make_user(Role.LENDER)

    with pytest.raises(AccountError, match="bereits"):
        change_role(user, Role.LENDER, actor=admin, reason="nichts")


@pytest.mark.parametrize("role", OTHER_ROLES)
def test_only_admin_changes_roles(role: Role, make_user: MakeUser) -> None:
    """Verbotener Fall, auch für sich selbst: niemand außer Admin vergibt Rollen."""
    actor = make_user(role)

    with pytest.raises(PermissionDenied):
        change_role(actor, Role.ADMIN, actor=actor, reason="Selbstbedienung")

    actor.refresh_from_db()
    assert actor.role == role
    assert not AccountEvent.objects.exists()


def test_admin_cannot_demote_self(admin: User, make_user: MakeUser) -> None:
    make_user(Role.ADMIN)

    with pytest.raises(AccountError, match="eigene"):
        change_role(admin, Role.VIEWER, actor=admin, reason="Test")

    admin.refresh_from_db()
    assert admin.role == Role.ADMIN


def test_last_admin_keeps_role(admin: User, make_user: MakeUser) -> None:
    """Das letzte aktive Admin-Konto bleibt erhalten, auch wenn ein Admin es ändert."""
    other = make_user(Role.ADMIN)
    change_role(other, Role.VIEWER, actor=admin, reason="Wechsel")
    make_user(Role.ADMIN, is_active=False)

    with pytest.raises(AccountError, match="letzte"):
        change_role(admin, Role.VIEWER, actor=None, reason="Befehl")

    admin.refresh_from_db()
    assert admin.role == Role.ADMIN


def test_admin_demotes_other_admin(admin: User, make_user: MakeUser) -> None:
    other = make_user(Role.ADMIN)

    change_role(other, Role.PPE_EXPERT, actor=admin, reason="nur noch Prüfer")

    other.refresh_from_db()
    assert other.role == Role.PPE_EXPERT


# --- Deaktivieren und reaktivieren ---


def test_deactivate_and_reactivate(admin: User, make_user: MakeUser) -> None:
    user = make_user(Role.LENDER)

    deactivate(user, actor=admin, reason="Austritt")
    user.refresh_from_db()
    assert not user.is_active
    reactivate(user, actor=admin, reason="Wiedereintritt")
    user.refresh_from_db()

    assert user.is_active
    assert [kind for kind, _, _ in events(user)] == [
        AccountEventKind.DEACTIVATED,
        AccountEventKind.REACTIVATED,
    ]


def test_deactivated_account_loses_open_links(admin: User, make_user: MakeUser) -> None:
    user = make_user(Role.LENDER)
    token = issue_enrollment(user, actor=admin)

    deactivate(user, actor=admin, reason="Austritt")
    reactivate(user, actor=admin, reason="Irrtum")

    assert find_token(token) is None


def test_admin_cannot_deactivate_self(admin: User, make_user: MakeUser) -> None:
    make_user(Role.ADMIN)

    with pytest.raises(AccountError, match="eigene"):
        deactivate(admin, actor=admin, reason="Test")

    admin.refresh_from_db()
    assert admin.is_active


def test_last_admin_cannot_be_deactivated(admin: User, make_user: MakeUser) -> None:
    other = make_user(Role.ADMIN)
    deactivate(other, actor=admin, reason="Austritt")

    with pytest.raises(AccountError, match="letzte"):
        deactivate(admin, actor=None, reason="Befehl")

    admin.refresh_from_db()
    assert admin.is_active


@pytest.mark.parametrize("role", OTHER_ROLES)
def test_only_admin_deactivates(role: Role, make_user: MakeUser) -> None:
    user = make_user(Role.VIEWER)

    with pytest.raises(PermissionDenied):
        deactivate(user, actor=make_user(role), reason="Test")

    user.refresh_from_db()
    assert user.is_active
    assert not AccountEvent.objects.exists()


@pytest.mark.parametrize("reason", ["", " "])
def test_deactivate_needs_reason(reason: str, admin: User, make_user: MakeUser) -> None:
    user = make_user(Role.VIEWER)

    with pytest.raises(AccountError, match="Begründung"):
        deactivate(user, actor=admin, reason=reason)

    user.refresh_from_db()
    assert user.is_active


def test_reactivate_active_account_is_rejected(admin: User, make_user: MakeUser) -> None:
    with pytest.raises(AccountError, match="aktiv"):
        reactivate(make_user(Role.VIEWER), actor=admin, reason="Test")


# --- Sachkunde ---


def test_grant_qualification(admin: User, make_user: MakeUser) -> None:
    expert = make_user(Role.PPE_EXPERT)
    today = timezone.localdate()

    grant_qualification(
        expert,
        valid_from=today,
        valid_until=today + timedelta(days=365),
        evidence="Lehrgang PSA-Sachkunde (Testdaten)",
        actor=admin,
    )

    assert expert.may_inspect()
    assert Qualification.objects.get().created_by == admin


@pytest.mark.parametrize(
    ("valid_from", "valid_until", "evidence", "message"),
    [
        (date(2026, 10, 1), date(2026, 9, 30), "Lehrgang", "vor"),
        (date(2026, 10, 1), date(2027, 9, 30), "  ", "Nachweis"),
    ],
)
def test_grant_qualification_checks_input(
    valid_from: date,
    valid_until: date,
    evidence: str,
    message: str,
    admin: User,
    make_user: MakeUser,
) -> None:
    with pytest.raises(AccountError, match=message):
        grant_qualification(
            make_user(Role.PPE_EXPERT),
            valid_from=valid_from,
            valid_until=valid_until,
            evidence=evidence,
            actor=admin,
        )

    assert not Qualification.objects.exists()


@pytest.mark.parametrize("role", OTHER_ROLES)
def test_only_admin_grants_qualification(role: Role, make_user: MakeUser) -> None:
    """Verbotener Fall: Auch PSA-Sachkundige tragen sich keine Sachkunde selbst ein."""
    actor = make_user(role)
    today = timezone.localdate()

    with pytest.raises(PermissionDenied):
        grant_qualification(
            actor,
            valid_from=today,
            valid_until=today + timedelta(days=365),
            evidence="Selbst",
            actor=actor,
        )

    assert not Qualification.objects.exists()


def test_revoke_qualification(admin: User, make_user: MakeUser) -> None:
    expert = make_user(Role.PPE_EXPERT)
    today = timezone.localdate()
    entry = grant_qualification(
        expert,
        valid_from=today,
        valid_until=today + timedelta(days=365),
        evidence="Lehrgang",
        actor=admin,
    )

    revoke_qualification(entry, actor=admin, reason="Nachweis zurückgezogen")

    assert not expert.may_inspect()
    assert QualificationRevocation.objects.get().created_by == admin
    with pytest.raises(AccountError, match="bereits"):
        revoke_qualification(entry, actor=admin, reason="nochmal")


@pytest.mark.parametrize("role", OTHER_ROLES)
def test_only_admin_revokes_qualification(role: Role, admin: User, make_user: MakeUser) -> None:
    expert = make_user(Role.PPE_EXPERT)
    today = timezone.localdate()
    entry = grant_qualification(
        expert, valid_from=today, valid_until=today, evidence="Lehrgang", actor=admin
    )

    with pytest.raises(PermissionDenied):
        revoke_qualification(entry, actor=make_user(role), reason="Test")

    assert not QualificationRevocation.objects.exists()


def test_revocation_needs_reason(admin: User, make_user: MakeUser) -> None:
    today = timezone.localdate()
    entry = grant_qualification(
        make_user(Role.PPE_EXPERT),
        valid_from=today,
        valid_until=today,
        evidence="Lehrgang",
        actor=admin,
    )

    with pytest.raises(AccountError, match="Begründung"):
        revoke_qualification(entry, actor=admin, reason=" ")

    assert not QualificationRevocation.objects.exists()


# --- Passkey widerrufen (verlorener Schlüssel) ---


def _passkey(user: User, name: str = "YubiKey") -> Passkey:
    return Passkey.objects.create(
        user=user, name=name, credential_id=name.encode(), public_key=b"\x00", sign_count=0
    )


def test_admin_revokes_lost_passkey_even_the_last(admin: User, make_user: MakeUser) -> None:
    """Ein verlorener Schlüssel wird sofort ungültig; danach hilft ein Einrichtungslink."""
    expert = make_user(Role.PPE_EXPERT)
    passkey = _passkey(expert)

    revoke_passkey(passkey, actor=admin, reason="Schlüssel verloren")

    passkey.refresh_from_db()
    assert passkey.revoked_at is not None
    event = expert.account_events.get()
    assert (event.kind, event.reason) == (
        AccountEventKind.PASSKEY_REVOKED,
        "Schlüssel verloren (Passkey „YubiKey“)",
    )


@pytest.mark.parametrize("role", OTHER_ROLES)
def test_only_admin_revokes_passkeys_of_others(role: Role, make_user: MakeUser) -> None:
    owner = make_user(Role.LENDER)
    passkey = _passkey(owner)

    with pytest.raises(PermissionDenied):
        revoke_passkey(passkey, actor=make_user(role), reason="Test")

    passkey.refresh_from_db()
    assert passkey.revoked_at is None


def test_admin_keeps_own_last_passkey(admin: User) -> None:
    """Sonst sperrte sich der Admin aus; mit einem zweiten Passkey geht es."""
    first = _passkey(admin, "YubiKey 1")

    with pytest.raises(AccountError, match="letzter Passkey"):
        revoke_passkey(first, actor=admin, reason="Test")
    _passkey(admin, "YubiKey 2")
    revoke_passkey(first, actor=admin, reason="Schlüssel verloren")

    first.refresh_from_db()
    assert first.revoked_at is not None


def test_revoke_passkey_needs_reason(admin: User, make_user: MakeUser) -> None:
    passkey = _passkey(make_user(Role.LENDER))

    with pytest.raises(AccountError, match="Begründung"):
        revoke_passkey(passkey, actor=admin, reason="")

    passkey.refresh_from_db()
    assert passkey.revoked_at is None


# --- Protokoll ---


def test_account_events_are_append_only(admin: User, make_user: MakeUser) -> None:
    user = make_user(Role.LENDER)
    change_role(user, Role.TRAINER, actor=admin, reason="Wechsel")
    event = user.account_events.get()

    with pytest.raises(AppendOnlyError):
        AccountEvent.objects.filter(pk=event.pk).update(reason="anders")
    with pytest.raises(AppendOnlyError):
        event.delete()


def test_role_at_a_point_in_time_is_traceable(admin: User, make_user: MakeUser) -> None:
    """Aus dem Protokoll lässt sich die Rolle zu jedem Zeitpunkt ablesen."""
    user, _ = create_account(
        "pruefer.neu", first_name="Neu", last_name="Prüfer", role=Role.LENDER, actor=admin
    )
    change_role(user, Role.PPE_EXPERT, actor=admin, reason="Sachkunde erworben")

    history = [
        (event.new_role or None)
        for event in user.account_events.filter(
            kind__in=[AccountEventKind.CREATED, AccountEventKind.ROLE_CHANGED]
        )
    ]

    assert history == [Role.LENDER, Role.PPE_EXPERT]
