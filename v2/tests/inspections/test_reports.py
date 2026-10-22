"""Prüfberichte: Liste, Details, signiertes PDF und Prüfknopf „unverändert?“ (Parität 9, #39).

Festlegungen (Felix, 21.10.2026): alle Rollen sehen die Berichte; Altberichte in derselben Liste,
markiert, ohne Prüfknopf; Filter nach Zeitraum, Sachkundiger Person und Teil/EI.
"""

import hashlib
import json
from datetime import timedelta

import pytest
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts import certificates
from accounts.models import QualificationRevocation, Role, User
from inspections import reports, services
from inspections.models import InspectionRound, Outcome, RoundProtocol, SignedProtocol
from inspections.reports import Verdict
from inspections.signature import stamp
from legacy.models import LegacyReport, LegacyReportItem
from tests.helpers import make_inspector, tamper
from tests.inspections.conftest import MakePart, in_days
from tests.signing import sign, sign_pdf, signing_environment, yubikey_of

pytestmark = pytest.mark.django_db


def signed_round(inspector: User, make_part: MakePart, **part: str) -> InspectionRound:
    round_ = services.start_round(actor=inspector)
    services.record_result(
        round_, make_part(**part), Outcome.PASSED, actor=inspector, next_due=in_days(365)
    )
    services.close_round(round_, actor=inspector)
    sign(round_, actor=inspector)
    return round_


def legacy_report(make_part: MakePart) -> LegacyReport:
    report = LegacyReport.objects.create(
        legacy_id=7,
        created_at_raw="01.03.2025 10:00",
        created_at=timezone.now() - timedelta(days=500),
        inspector_name="Testperson Altbestand",
        confirmed=True,
        signature_data=json.dumps([[[10, 10], [50, 40], [90, 15]]]),
    )
    LegacyReportItem.objects.create(
        report=report,
        legacy_id=70,
        item_label="Alter Helm (TST-ALT1)",
        inspection_done=True,
        part=make_part(designation="Helm", ei="TST-ALT1"),
    )
    return report


def check(round_: InspectionRound) -> reports.CheckResult:
    with signing_environment():
        return reports.check(SignedProtocol.objects.get(round=round_))


# --- Liste und Filter ---


def test_parity_9_list_shows_signed_and_legacy_reports(
    client: Client, inspector: User, make_part: MakePart
) -> None:
    round_ = signed_round(inspector, make_part)
    legacy = legacy_report(make_part)
    client.force_login(make_inspector(role=Role.VIEWER, qualified=False))

    page = client.get(reverse("inspections:reports")).content.decode()

    assert reverse("inspections:report_detail", args=[round_.pk]) in page
    assert reverse("inspections:legacy_report_detail", args=[legacy.pk]) in page
    assert "Altbestand, ungesichert" in page and "signiert" in page


@pytest.mark.parametrize(
    ("filters", "expected"),
    [
        ({"inspector": "Prüfer"}, {"neu"}),
        ({"inspector": "altbestand"}, {"alt"}),
        ({"part": "tst-neu 1"}, {"neu"}),
        ({"part": "TST-ALT1"}, {"alt"}),
        ({"start": "today-10"}, {"neu"}),
        ({"end": "today-10"}, {"alt"}),
    ],
)
def test_parity_9_filters(
    filters: dict[str, str], expected: set[str], inspector: User, make_part: MakePart
) -> None:
    signed_round(inspector, make_part, ei="TST-NEU1")
    legacy_report(make_part)
    resolved = {
        key: timezone.localdate() - timedelta(days=10) if value == "today-10" else value
        for key, value in filters.items()
    }

    entries = reports.all_reports(**resolved)

    assert {"alt" if entry.legacy else "neu" for entry in entries} == expected


# --- Details und PDF ---


def test_parity_9_detail_and_signed_pdf(
    client: Client, inspector: User, make_part: MakePart
) -> None:
    round_ = signed_round(inspector, make_part, ei="TST-DET1")
    client.force_login(make_inspector(role=Role.LENDER, qualified=False))

    page = client.get(reverse("inspections:report_detail", args=[round_.pk])).content.decode()
    pdf = client.get(reverse("inspections:report_pdf", args=[round_.pk]))

    assert "TST-DET1" in page and Outcome.PASSED.label in page
    assert SignedProtocol.objects.get(round=round_).certificate.sha256 in page
    assert pdf["Content-Type"] == "application/pdf"
    assert pdf.content == bytes(SignedProtocol.objects.get(round=round_).pdf)


def test_parity_9_legacy_detail_shows_drawn_signature(client: Client, make_part: MakePart) -> None:
    report = legacy_report(make_part)
    client.force_login(make_inspector(role=Role.VIEWER, qualified=False))

    page = client.get(
        reverse("inspections:legacy_report_detail", args=[report.pk])
    ).content.decode()

    assert "Altbestand, ungesichert" in page
    assert "<svg" in page and "Alter Helm (TST-ALT1)" in page
    assert reverse("inspections:report_check", args=[report.pk]) not in page


def test_unsigned_round_is_no_report(client: Client, inspector: User, make_part: MakePart) -> None:
    round_ = services.start_round(actor=inspector)
    services.record_result(
        round_, make_part(), Outcome.PASSED, actor=inspector, next_due=in_days(365)
    )
    services.close_round(round_, actor=inspector)
    client.force_login(inspector)

    assert client.get(reverse("inspections:report_detail", args=[round_.pk])).status_code == 404
    assert reports.all_reports() == []


# --- Prüfknopf: unverändert ---


def test_parity_9_check_unchanged(client: Client, inspector: User, make_part: MakePart) -> None:
    round_ = signed_round(inspector, make_part)
    client.force_login(make_inspector(role=Role.VIEWER, qualified=False))

    with signing_environment():
        response = client.get(
            reverse("inspections:report_check", args=[round_.pk]), headers={"HX-Request": "true"}
        )

    page = response.content.decode()
    assert "Unverändert." in page and "Zeitstempel gültig" in page
    assert check(round_).verdict == Verdict.UNCHANGED


@pytest.mark.parametrize("later", ["Sachkunde widerrufen", "Rolle entzogen", "YubiKey gestrichen"])
def test_later_changes_do_not_spoil_old_reports(
    later: str, inspector: User, make_part: MakePart, settings: object
) -> None:
    """Rückblick: Gilt der Bericht beim Hochladen, bleibt er gültig."""
    round_ = signed_round(inspector, make_part)
    if later == "Sachkunde widerrufen":
        QualificationRevocation.objects.create(
            qualification=inspector.qualifications.get(), reason="Test"
        )
    elif later == "Rolle entzogen":
        User.objects.filter(pk=inspector.pk).update(role=Role.VIEWER)

    if later == "YubiKey gestrichen":
        with signing_environment(), override_settings(PSAGENT_YUBIKEY_SERIALS=["4711"]):
            result = reports.check(SignedProtocol.objects.get(round=round_))
    else:
        result = check(round_)

    assert result.verdict == Verdict.UNCHANGED, result.findings


# --- Prüfknopf: verändert ---


def test_changed_pdf_in_database_is_found(inspector: User, make_part: MakePart) -> None:
    round_ = signed_round(inspector, make_part)
    entry = SignedProtocol.objects.get(round=round_)
    pdf = bytearray(bytes(entry.pdf))
    pdf[-50] = (pdf[-50] + 1) % 256

    tamper(
        "inspections_signedprotocol",
        "UPDATE inspections_signedprotocol SET pdf = %s WHERE id = %s",
        [bytes(pdf), entry.pk],
    )

    result = check(round_)
    assert result.verdict == Verdict.CHANGED
    assert any("signierte PDF wurde in der Datenbank verändert" in f for f in result.findings)


def test_changed_result_is_found(inspector: User, make_part: MakePart) -> None:
    round_ = signed_round(inspector, make_part)
    result_row = round_.results.get()

    tamper(
        "inspections_inspectionresult",
        "UPDATE inspections_inspectionresult SET comment = %s WHERE id = %s",
        ["nachträglich geändert", result_row.pk],
    )

    result = check(round_)
    assert result.verdict == Verdict.CHANGED
    assert any("Ergebnisse der Runde" in f for f in result.findings)
    assert any("Hash-Kette" in f for f in result.findings)


def test_recomputed_chain_is_found_by_protocol_head(inspector: User, make_part: MakePart) -> None:
    round_ = signed_round(inspector, make_part)
    head = RoundProtocol.objects.get(round=round_)

    tamper(
        "core_chainlink",
        "UPDATE core_chainlink SET hash = %s WHERE seq = %s",
        ["e" * 64, head.chain_seq],
    )

    result = check(round_)
    assert result.verdict == Verdict.CHANGED
    assert any(f"Kettenkopf {head.chain_seq}" in f for f in result.findings)


def test_changed_stamp_time_is_found(inspector: User, make_part: MakePart) -> None:
    round_ = signed_round(inspector, make_part)
    entry = SignedProtocol.objects.get(round=round_)

    tamper(
        "inspections_signedprotocol",
        "UPDATE inspections_signedprotocol SET stamped_at = %s WHERE id = %s",
        [entry.stamped_at - timedelta(days=30), entry.pk],
    )

    result = check(round_)
    assert result.verdict == Verdict.CHANGED
    assert any("weicht vom Zeitstempel ab" in f for f in result.findings)


def test_certificate_revoked_before_upload_is_found(inspector: User, make_part: MakePart) -> None:
    round_ = signed_round(inspector, make_part)
    entry = SignedProtocol.objects.get(round=round_)
    certificates.revoke(
        entry.certificate,
        actor=None,
        reason="YubiKey schon vorher verloren",
        revoked_at=entry.signed_at - timedelta(minutes=1),
    )

    result = check(round_)
    assert result.verdict == Verdict.CHANGED
    assert any("widerrufen" in f for f in result.findings)


# --- Prüfknopf: nicht prüfbar ---


def test_missing_timestamp_settings_make_it_uncheckable(
    inspector: User, make_part: MakePart
) -> None:
    round_ = signed_round(inspector, make_part)

    with signing_environment(), override_settings(PSAGENT_TSA_CA_PATH=""):
        result = reports.check(SignedProtocol.objects.get(round=round_))

    assert result.verdict == Verdict.UNCHECKABLE
    assert any("PSAGENT_TSA_CA_PATH" in reason for reason in result.unchecked)


def test_timestamp_before_signature_is_found(inspector: User, make_part: MakePart) -> None:
    """Stempel zuerst, Signatur später: Die Zeit des Stempels belegt die Signatur nicht."""
    round_ = signed_round(inspector, make_part)
    entry = SignedProtocol.objects.get(round=round_)
    with signing_environment():
        stamped, stamped_at = stamp(bytes(entry.protocol.pdf))
        pdf = sign_pdf(stamped, yubikey_of(inspector))
    tamper(
        "inspections_signedprotocol",
        "UPDATE inspections_signedprotocol SET pdf = %s, sha256 = %s, stamped_at = %s"
        " WHERE id = %s",
        [pdf, hashlib.sha256(pdf).hexdigest(), stamped_at, entry.pk],
    )

    result = check(round_)
    assert result.verdict == Verdict.CHANGED
    assert "Der Zeitstempel liegt vor der Signatur und deckt sie nicht ab." in result.findings
