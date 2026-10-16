"""PSA-relevante Änderungen nur mit gültiger Sachkunde (Abnahme #8, verbotener Fall).

Gilt für Prüfen und Signieren, Entsperren, Aussondern, „keine PSA“ setzen und aufheben,
EI, Herstellungsjahr und Datum 1. Einsatz ändern. „Nächste Prüfung“ setzt nur eine Prüfung.
Sperren bleibt ohne Sachkunde möglich (CLAUDE.md, Fachregeln).
"""

from collections.abc import Callable
from datetime import date, timedelta

import pytest
from django.core.exceptions import PermissionDenied
from django.utils import timezone

from accounts.models import Qualification, QualificationRevocation, Role, User
from inspections.models import InspectionRound, Outcome, RoundState
from inspections.services import close_round, record_result, sign_round, start_round
from inventory.models import EventKind, PartStatus
from inventory.services import InventoryError, record_event, update_component
from tests.helpers import make_inspector, signed_inspection
from tests.inspections.conftest import MakePart, in_days


def _expired() -> User:
    user = make_inspector(qualified=False)
    today = timezone.localdate()
    Qualification.objects.create(
        user=user,
        valid_from=today - timedelta(days=800),
        valid_until=today - timedelta(days=1),
        evidence="abgelaufen (Testdaten)",
    )
    return user


def _revoked() -> User:
    user = make_inspector()
    QualificationRevocation.objects.create(
        qualification=user.qualifications.get(), reason="Widerruf (Testdaten)"
    )
    return user


UNQUALIFIED: dict[str, Callable[[], User]] = {
    "ohne Sachkunde": lambda: make_inspector(qualified=False),
    "Sachkunde abgelaufen": _expired,
    "Sachkunde widerrufen": _revoked,
    "Trainer mit Sachkunde": lambda: make_inspector(role=Role.TRAINER),
    "Admin ohne Sachkunde": lambda: make_inspector(qualified=False, role=Role.ADMIN),
    "Konto deaktiviert": lambda: make_inspector(is_active=False),
}


@pytest.fixture(params=list(UNQUALIFIED))
def unqualified(request: pytest.FixtureRequest, db: None) -> User:
    return UNQUALIFIED[request.param]()


def test_admin_with_qualification_may_inspect(make_part: MakePart) -> None:
    admin = make_inspector(role=Role.ADMIN)

    signed_inspection(admin, [make_part()])


def test_round_cannot_be_started(unqualified: User) -> None:
    with pytest.raises(PermissionDenied, match="Sachkunde"):
        start_round(actor=unqualified)

    assert not InspectionRound.objects.exists()


def test_qualification_lost_before_signing(make_part: MakePart, inspector: User) -> None:
    """Die Sachkunde muss auch beim Signieren noch gültig sein."""
    part = make_part()
    round_ = start_round(actor=inspector)
    record_result(round_, part, Outcome.PASSED, actor=inspector, next_due=in_days(365))
    close_round(round_, actor=inspector)
    QualificationRevocation.objects.create(
        qualification=inspector.qualifications.get(), reason="Widerruf (Testdaten)"
    )

    with pytest.raises(PermissionDenied):
        sign_round(round_, actor=inspector)

    assert round_.state() == RoundState.CLOSED
    assert part.status() == PartStatus.NEW


def test_qualification_lost_while_recording(make_part: MakePart, inspector: User) -> None:
    round_ = start_round(actor=inspector)
    QualificationRevocation.objects.create(
        qualification=inspector.qualifications.get(), reason="Widerruf (Testdaten)"
    )

    with pytest.raises(PermissionDenied):
        record_result(round_, make_part(), Outcome.PASSED, actor=inspector, next_due=in_days(9))


@pytest.mark.parametrize(
    ("kind", "prepare"),
    [
        (EventKind.UNLOCKED, EventKind.LOCKED),
        (EventKind.RETIRED, None),
        (EventKind.NO_PPE_SET, None),
        (EventKind.NO_PPE_CLEARED, EventKind.NO_PPE_SET),
    ],
)
def test_ppe_events_need_qualification(
    kind: EventKind,
    prepare: EventKind | None,
    make_part: MakePart,
    inspector: User,
    unqualified: User,
) -> None:
    part = make_part()
    if prepare is not None:
        record_event(part, prepare, actor=inspector, comment="Vorbereitung")
    count = part.events.count()

    with pytest.raises(PermissionDenied, match="Sachkunde"):
        record_event(part, kind, actor=unqualified, comment="ohne Sachkunde")

    assert part.events.count() == count
    record_event(part, kind, actor=inspector, comment="mit Sachkunde")


def test_lock_needs_no_qualification(make_part: MakePart) -> None:
    lender = User.objects.create_user(username="verleih", role=Role.LENDER)
    part = make_part()

    record_event(part, EventKind.LOCKED, actor=lender, comment="Riss entdeckt")

    assert part.status() == PartStatus.LOCKED


def test_release_only_through_inspection(make_part: MakePart, inspector: User) -> None:
    """Neue Teile sind erst nach der ersten PSA-Prüfung verleihbar."""
    part = make_part()

    with pytest.raises(InventoryError, match="nur über eine PSA-Prüfung"):
        record_event(part, EventKind.RELEASED, actor=inspector)

    assert part.status() == PartStatus.NEW


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("ei", "TST-NEU-1"),
        ("year_of_manufacture", 2020),
        ("first_use_date", date(2024, 5, 1)),
    ],
)
def test_ppe_fields_need_qualification(
    field: str, value: object, make_part: MakePart, inspector: User, unqualified: User
) -> None:
    component = make_part(
        year_of_manufacture=2021, first_use_date=date(2023, 1, 1)
    ).components.get()

    with pytest.raises(PermissionDenied, match="Sachkunde"):
        update_component(component, actor=unqualified, **{field: value})

    component.refresh_from_db()
    assert getattr(component, field) != value
    update_component(component, actor=inspector, **{field: value})
    component.refresh_from_db()
    assert getattr(component, field) == value


def test_other_fields_need_no_qualification(make_part: MakePart) -> None:
    trainer = User.objects.create_user(username="trainer", role=Role.TRAINER)
    component = make_part().components.get()

    update_component(component, actor=trainer, manufacturer="Beispiel Alpin", serial_number="SN-9")

    component.refresh_from_db()
    assert (component.manufacturer, component.serial_number) == ("Beispiel Alpin", "SN-9")


def test_unchanged_ppe_field_needs_no_qualification(make_part: MakePart) -> None:
    trainer = User.objects.create_user(username="trainer", role=Role.TRAINER)
    component = make_part().components.get()

    update_component(component, actor=trainer, ei=component.ei, manufacturer="Beispiel Alpin")


def test_next_due_is_not_a_component_field(make_part: MakePart, inspector: User) -> None:
    """Parität 3: „nächste Prüfung“ nur über eine Prüfung."""
    component = make_part().components.get()

    with pytest.raises(InventoryError, match="Nicht änderbare Felder"):
        update_component(component, actor=inspector, next_due=in_days(30))


def test_updated_ei_stays_unique(make_part: MakePart, inspector: User) -> None:
    taken = make_part().components.get().ei
    component = make_part().components.get()

    with pytest.raises(Exception, match="inventory_component_ei_unique"):
        update_component(component, actor=inspector, ei=f" {taken.lower()} ")


def test_retired_part_cannot_be_edited(make_part: MakePart, inspector: User) -> None:
    part = make_part()
    record_event(part, EventKind.RETIRED, actor=inspector, comment="Ablegereife")

    with pytest.raises(InventoryError, match="ausgesondert"):
        update_component(part.components.get(), actor=inspector, manufacturer="egal")
