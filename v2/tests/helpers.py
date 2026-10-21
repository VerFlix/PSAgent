"""Hilfen, die mehrere Testbereiche brauchen. Nur künstliche Testdaten."""

import itertools
from collections.abc import Sequence
from datetime import date, timedelta
from typing import Any

from django.db import connection
from django.utils import timezone

from accounts.models import Qualification, Role, User
from inspections.models import InspectionRound, Outcome
from inspections.services import (
    adopt_legacy_round,
    close_round,
    record_result,
    start_round,
)
from inventory.models import Part
from tests.signing import sign

_numbers = itertools.count(1)


def make_inspector(*, qualified: bool = True, role: Role = Role.PPE_EXPERT, **fields: Any) -> User:
    """Konto mit Rolle PSA-Sachkundig und (Standard) heute gültiger Sachkunde."""
    number = next(_numbers)
    user = User.objects.create_user(
        username=f"pruefer{number}",
        first_name="Testperson",
        last_name=f"Prüfer {number}",
        role=role,
        **fields,
    )
    if qualified:
        today = timezone.localdate()
        Qualification.objects.create(
            user=user,
            valid_from=today - timedelta(days=365),
            valid_until=today + timedelta(days=365),
            evidence="Lehrgang PSA-Sachkunde (Testdaten)",
        )
    return user


def tamper(table: str, sql: str, params: list[Any] | None = None) -> None:
    """Ändert eine Tabelle an Trigger und App vorbei, wie ein Angreifer mit Datenbankzugang."""
    with connection.cursor() as cursor:
        # Im Test läuft alles in einer Transaktion; ausstehende Prüfungen erst abschließen.
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
        cursor.execute(f'ALTER TABLE "{table}" DISABLE TRIGGER "{table}_append_only"')
        cursor.execute(sql, params or [])
        cursor.execute(f'ALTER TABLE "{table}" ENABLE TRIGGER "{table}_append_only"')


def adopt_inspection(
    parts: Sequence[Part], *, next_due: date, outcome: Outcome = Outcome.PASSED, comment: str = ""
) -> InspectionRound:
    """Übernimmt eine Prüfung wie aus dem Altsystem (ohne Konto, ohne Signatur)."""
    round_ = start_round(actor=None, inspector_name="Testperson Altprüfer")
    for part in parts:
        record_result(round_, part, outcome, actor=None, comment=comment, next_due=next_due)
    close_round(round_, actor=None)
    adopt_legacy_round(round_)
    return round_


def signed_inspection(
    inspector: User,
    parts: Sequence[Part],
    *,
    outcome: Outcome = Outcome.PASSED,
    next_due: date | None = None,
    comment: str = "",
) -> InspectionRound:
    """Prüfungsrunde eines Sachkundigen: erfassen, abschließen, signieren."""
    if next_due is None and outcome != Outcome.RETIRE:
        next_due = timezone.localdate() + timedelta(days=365)
    round_ = start_round(actor=inspector)
    for part in parts:
        record_result(round_, part, outcome, actor=inspector, comment=comment, next_due=next_due)
    close_round(round_, actor=inspector)
    sign(round_, actor=inspector)
    return round_
