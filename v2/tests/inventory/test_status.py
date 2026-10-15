"""Status eines Teils, abgeleitet aus Vorgängen (Konzept, Abschnitt 4).

Neue Teile sind „neu, nicht freigegeben“ und erst nach der ersten PSA-Prüfung (oder „keine
PSA“) verleihbar. Sperren, Entsperren und Aussondern brauchen einen Kommentar.
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from django.db import IntegrityError, transaction

from accounts.models import User
from inventory.models import EventKind, PartEvent, PartStatus
from inventory.services import InventoryError, record_event
from tests.inventory.conftest import MakeProduct

T0 = datetime(2026, 10, 15, 9, 0, tzinfo=ZoneInfo("Europe/Berlin"))
HOUR = timedelta(hours=1)


def test_new_part_is_not_released(make_product: MakeProduct) -> None:
    part = make_product()

    assert part.status() == PartStatus.NEW
    assert part.status().label == "neu, nicht freigegeben"
    assert part.is_released() is False


@pytest.mark.parametrize(
    ("kind", "by_actor"), [(EventKind.RELEASED, False), (EventKind.NO_PPE_SET, True)]
)
def test_release_by_import_or_no_ppe(
    kind: EventKind, by_actor: bool, make_product: MakeProduct, actor: User
) -> None:
    """Freigabe durch Prüfung testet `tests/inspections`; hier Import und „keine PSA“."""
    part = make_product()
    record_event(part, kind, actor=actor if by_actor else None)

    assert part.is_released() is True
    assert part.status() == PartStatus.RELEASED


def test_lock_and_unlock(make_product: MakeProduct, actor: User) -> None:
    part = make_product()
    record_event(part, EventKind.RELEASED, actor=None, at=T0)
    record_event(part, EventKind.LOCKED, actor=actor, comment="Naht aufgerissen", at=T0 + HOUR)
    record_event(
        part, EventKind.UNLOCKED, actor=actor, comment="Nachprüfung o. B.", at=T0 + 2 * HOUR
    )

    assert part.status(at=T0 + HOUR) == PartStatus.LOCKED
    assert part.is_locked(at=T0 + HOUR) is True
    assert part.status() == PartStatus.RELEASED


def test_new_part_can_be_locked(make_product: MakeProduct, actor: User) -> None:
    """Sperren geht jederzeit, auch vor der Freigabe."""
    part = make_product()
    record_event(part, EventKind.LOCKED, actor=actor, comment="Hersteller-Rückruf")

    assert part.status() == PartStatus.LOCKED
    assert part.is_released() is False


def test_retired_part_stays_retired(make_product: MakeProduct, actor: User) -> None:
    part = make_product()
    record_event(part, EventKind.RELEASED, actor=None)
    record_event(part, EventKind.RETIRED, actor=actor, comment="Ablegereife erreicht")

    assert part.status() == PartStatus.RETIRED
    for kind in EventKind:
        with pytest.raises(InventoryError, match="ausgesondert"):
            record_event(part, kind, actor=actor, comment="danach")


@pytest.mark.parametrize("kind", [EventKind.LOCKED, EventKind.RETIRED])
@pytest.mark.parametrize("comment", ["", "  "])
def test_comment_is_mandatory(
    kind: EventKind, comment: str, make_product: MakeProduct, actor: User
) -> None:
    part = make_product()

    with pytest.raises(InventoryError, match="Kommentar"):
        record_event(part, kind, actor=actor, comment=comment)
    with pytest.raises(IntegrityError), transaction.atomic():
        PartEvent.objects.create(part=part, kind=kind, actor=actor, comment=comment)


def test_unlock_needs_a_comment(make_product: MakeProduct, actor: User) -> None:
    part = make_product()
    record_event(part, EventKind.LOCKED, actor=actor, comment="gesperrt")

    with pytest.raises(InventoryError, match="Kommentar"):
        record_event(part, EventKind.UNLOCKED, actor=actor)


def test_lock_twice_or_unlock_unlocked_is_rejected(make_product: MakeProduct, actor: User) -> None:
    part = make_product()

    with pytest.raises(InventoryError, match="nicht gesperrt"):
        record_event(part, EventKind.UNLOCKED, actor=actor, comment="grundlos")
    record_event(part, EventKind.LOCKED, actor=actor, comment="erste Sperre")
    with pytest.raises(InventoryError, match="bereits gesperrt"):
        record_event(part, EventKind.LOCKED, actor=actor, comment="zweite Sperre")


def test_release_only_once(make_product: MakeProduct, actor: User) -> None:
    part = make_product()
    record_event(part, EventKind.RELEASED, actor=None)

    with pytest.raises(InventoryError, match="bereits freigegeben"):
        record_event(part, EventKind.RELEASED, actor=None)
    with pytest.raises(IntegrityError), transaction.atomic():
        PartEvent.objects.create(part=part, kind=EventKind.RELEASED, actor=actor)


def test_no_ppe_can_be_cleared(make_product: MakeProduct, actor: User) -> None:
    part = make_product()
    record_event(part, EventKind.NO_PPE_SET, actor=actor, at=T0)
    record_event(part, EventKind.NO_PPE_CLEARED, actor=actor, at=T0 + HOUR)

    assert part.has_no_ppe(at=T0) is True
    assert part.has_no_ppe() is False
    assert part.is_released() is True  # freigegeben bleibt freigegeben


def test_events_are_listed_in_order(make_product: MakeProduct, actor: User) -> None:
    part = make_product()
    record_event(part, EventKind.LOCKED, actor=actor, comment="b", at=T0 + HOUR)
    record_event(part, EventKind.UNLOCKED, actor=actor, comment="c", at=T0 + 2 * HOUR)

    assert [event.comment for event in part.events.all()] == ["b", "c"]


def test_events_cannot_be_changed_or_deleted(make_product: MakeProduct, actor: User) -> None:
    part = make_product()
    event = record_event(part, EventKind.LOCKED, actor=actor, comment="gesperrt")

    with pytest.raises(IntegrityError):
        PartEvent.objects.filter(pk=event.pk).delete()
    with pytest.raises(IntegrityError, match="nur zum Anhängen"), transaction.atomic():
        PartEvent.objects.filter(pk=event.pk)._raw_delete(using="default")

    assert part.events.count() == 1
