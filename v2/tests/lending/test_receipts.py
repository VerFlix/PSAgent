"""Quittung per E-Mail nach Ausgabe und Rückgabe, auf Wunsch (#72, Konzept Abschnitt 6).

Die Quittung geht an die hinterlegte Adresse; die Meldung nennt die Adresse nicht, denn
Verleiher sehen Kontaktdaten nur bei laufender Ausleihe. Inhalt: Teile mit EI, Zeitraum, Links
zur GAL, Vorgangsnummer, Datenschutzhinweis; keine Unterschrift. Ein Fehler beim Versand
blockiert den Vorgang nicht.
"""

from typing import Any

import pytest
from django.core import mail
from django.test import Client, override_settings
from django.urls import reverse

from accounts.models import Role, User
from lending.models import Booking, Borrower, Loan, LoanStatus
from lending.receipts import ReceiptKind, build
from lending.services import create_borrower, lend, reserve
from privacy.notice import retention_notice
from tests.lending.conftest import DAY, TODAY, MakePart, checkout_kwargs
from tests.lending.test_booking_views import checkout_data, return_data

pytestmark = pytest.mark.django_db
FAILING = {"default": {"BACKEND": "tests.lending.test_receipts.FailingBackend"}}


class FailingBackend:
    def __init__(self, **kwargs: Any) -> None:
        pass

    def send_messages(self, messages: Any) -> int:
        raise OSError("Mailserver nicht erreichbar")


@pytest.fixture
def lender_client(client: Client, lender: User) -> Client:
    client.force_login(lender)
    return client


def checkout_post(booking: Booking, **extra: Any) -> dict[str, Any]:
    return checkout_data(booking, **extra)


def return_post(booking: Booking, **extra: Any) -> dict[str, Any]:
    return return_data(booking, comment="alles in Ordnung", **extra)


def reserved(make_part: MakePart, borrower: Borrower, lender: User) -> Booking:
    return reserve(borrower, [make_part()], TODAY, TODAY + 2 * DAY, actor=lender)


# --- Inhalt ---


def test_checkout_receipt_content(make_part: MakePart, borrower: Borrower, lender: User) -> None:
    part = make_part(model_name="Testmodell")
    booking = lend(borrower, [part], TODAY, TODAY + 2 * DAY, **checkout_kwargs(lender))

    message = build(booking, ReceiptKind.CHECKOUT)

    assert message is not None
    assert message.to == ["kim.beispiel@example.org"]
    assert message.subject == f"Quittung: Ausgabe, Ausleihvorgang #{booking.pk}"
    body = message.body
    assert "Hallo Kim Beispiel," in body
    assert f"{part.name} (EI {part.components.get().ei})" in body
    assert f"zurück bis {TODAY + 2 * DAY:%d.%m.%Y}" in body
    gal = part.documents.get()
    assert f"{gal.title}: {gal.url}" in body
    assert "Kurzkontrolle: ja · GAL bereitgestellt: ja" in body
    assert retention_notice() in body
    assert "10, 10" not in body and "[[" not in body  # keine Strichliste


def test_return_receipt_content(make_part: MakePart, borrower: Borrower, lender: User) -> None:
    from lending.services import return_loans
    from tests.lending.conftest import return_kwargs

    booking = lend(borrower, [make_part()], TODAY, TODAY + DAY, **checkout_kwargs(lender))
    return_loans(list(booking.loans.all()), **return_kwargs(lender), comment="Seil verdreht")

    message = build(booking, ReceiptKind.RETURN)

    assert message is not None
    assert message.subject == f"Quittung: Rückgabe, Ausleihvorgang #{booking.pk}"
    assert "zurückgenommen am" in message.body and "Seil verdreht" in message.body


def test_no_receipt_without_email(make_part: MakePart, lender: User) -> None:
    borrower = create_borrower("Noa Probe", address="x", phone="0000 1")
    booking = lend(borrower, [make_part()], TODAY, TODAY + DAY, **checkout_kwargs(lender))

    assert build(booking, ReceiptKind.CHECKOUT) is None


@override_settings(PSAGENT_CLUB_NAME="Beispielverein Sektion Musterstadt")
def test_receipt_names_the_club(make_part: MakePart, borrower: Borrower, lender: User) -> None:
    booking = lend(borrower, [make_part()], TODAY, TODAY + DAY, **checkout_kwargs(lender))

    message = build(booking, ReceiptKind.CHECKOUT)

    assert message is not None and "Beispielverein Sektion Musterstadt" in message.body


# --- Oberfläche ---


def test_receipt_on_request_at_checkout(
    lender_client: Client, make_part: MakePart, borrower: Borrower, lender: User
) -> None:
    booking = reserved(make_part, borrower, lender)
    url = reverse("lending:booking_checkout", args=[booking.pk])

    page = lender_client.get(url).content.decode()
    response = lender_client.post(url, checkout_post(booking, receipt="on"), follow=True)

    assert "Quittung per E-Mail an den Entleiher" in page
    assert len(mail.outbox) == 1
    text = response.content.decode()
    assert "Quittung per E-Mail gesendet." in text
    assert '<li class="message success">Quittung per E-Mail gesendet.</li>' in text


def test_no_receipt_without_tick(
    lender_client: Client, make_part: MakePart, borrower: Borrower, lender: User
) -> None:
    booking = reserved(make_part, borrower, lender)

    lender_client.post(
        reverse("lending:booking_checkout", args=[booking.pk]), checkout_post(booking)
    )

    assert mail.outbox == []


def test_receipt_on_request_at_return(
    lender_client: Client, make_part: MakePart, borrower: Borrower, lender: User
) -> None:
    booking = lend(borrower, [make_part()], TODAY, TODAY + DAY, **checkout_kwargs(lender))

    lender_client.post(
        reverse("lending:booking_return", args=[booking.pk]), return_post(booking, receipt="on")
    )

    [message] = mail.outbox
    assert "Rückgabe" in message.subject


def test_receipt_without_email_is_reported(
    lender_client: Client, make_part: MakePart, lender: User
) -> None:
    borrower = create_borrower("Noa Probe", address="x", phone="0000 1")
    booking = reserved(make_part, borrower, lender)

    response = lender_client.post(
        reverse("lending:booking_checkout", args=[booking.pk]),
        checkout_post(booking, receipt="on"),
        follow=True,
    )

    assert "keine E-Mail-Adresse hinterlegt" in response.content.decode()
    assert Loan.objects.get(booking=booking).status == LoanStatus.LENT


@override_settings(MAILERS=FAILING)
def test_failed_receipt_does_not_block_checkout(
    lender_client: Client, make_part: MakePart, borrower: Borrower, lender: User
) -> None:
    booking = reserved(make_part, borrower, lender)

    response = lender_client.post(
        reverse("lending:booking_checkout", args=[booking.pk]),
        checkout_post(booking, receipt="on"),
        follow=True,
    )

    assert Loan.objects.get(booking=booking).status == LoanStatus.LENT
    text = response.content.decode()
    assert "Quittung konnte nicht gesendet werden" in text
    assert "Quittung erneut senden" in text


def test_receipt_can_be_sent_again(
    lender_client: Client, make_part: MakePart, borrower: Borrower, lender: User
) -> None:
    booking = lend(borrower, [make_part()], TODAY, TODAY + DAY, **checkout_kwargs(lender))

    page = lender_client.get(reverse("lending:booking_detail", args=[booking.pk])).content.decode()
    lender_client.post(reverse("lending:booking_receipt", args=[booking.pk]), {"art": "ausgabe"})

    assert "Quittung erneut senden" in page
    [message] = mail.outbox
    assert "Ausgabe" in message.subject


def test_return_receipt_needs_a_return(
    lender_client: Client, make_part: MakePart, borrower: Borrower, lender: User
) -> None:
    booking = lend(borrower, [make_part()], TODAY, TODAY + DAY, **checkout_kwargs(lender))

    response = lender_client.post(
        reverse("lending:booking_receipt", args=[booking.pk]), {"art": "rueckgabe"}, follow=True
    )

    assert mail.outbox == []
    assert "Noch nichts zurückgenommen." in response.content.decode()


@pytest.mark.parametrize("role", [Role.VIEWER])
def test_viewer_cannot_send_receipts(
    role: Role, client: Client, make_part: MakePart, borrower: Borrower, lender: User
) -> None:
    """Verbotener Fall: Quittungen sendet nur, wer verleihen darf."""
    from tests.helpers import make_inspector

    booking = lend(borrower, [make_part()], TODAY, TODAY + DAY, **checkout_kwargs(lender))
    client.force_login(make_inspector(role=role, qualified=False))

    response = client.post(
        reverse("lending:booking_receipt", args=[booking.pk]), {"art": "ausgabe"}
    )

    assert response.status_code == 403 and mail.outbox == []


# --- Wer nachsenden darf ---


def returned_by(actor: User, make_part: MakePart, borrower: Borrower) -> Booking:
    from lending.services import return_loans
    from tests.lending.conftest import return_kwargs

    booking = lend(borrower, [make_part()], TODAY, TODAY + DAY, **checkout_kwargs(actor))
    return_loans(list(booking.loans.all()), **return_kwargs(actor))
    return booking


def test_own_return_receipt_within_a_day(
    lender_client: Client, make_part: MakePart, borrower: Borrower, lender: User
) -> None:
    booking = returned_by(lender, make_part, borrower)

    page = lender_client.get(reverse("lending:booking_detail", args=[booking.pk])).content.decode()
    lender_client.post(reverse("lending:booking_receipt", args=[booking.pk]), {"art": "rueckgabe"})

    assert "Quittung erneut senden (Rückgabe)" in page
    assert len(mail.outbox) == 1


def test_other_lender_cannot_resend_after_return(
    client: Client, make_part: MakePart, borrower: Borrower, lender: User
) -> None:
    """Verbotener Fall: Ohne Sicht auf die Kontaktdaten keine Mails an fremde Entleiher."""
    from tests.helpers import make_inspector

    booking = returned_by(lender, make_part, borrower)
    client.force_login(make_inspector(role=Role.LENDER, qualified=False))

    page = client.get(reverse("lending:booking_detail", args=[booking.pk])).content.decode()
    response = client.post(
        reverse("lending:booking_receipt", args=[booking.pk]), {"art": "rueckgabe"}
    )

    assert "Quittung erneut senden" not in page
    assert response.status_code == 403 and mail.outbox == []


def test_own_receipt_window_ends(
    lender_client: Client,
    make_part: MakePart,
    borrower: Borrower,
    lender: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import timedelta

    booking = returned_by(lender, make_part, borrower)
    monkeypatch.setattr("lending.receipts.RESEND_WINDOW", timedelta(0))  # Frist abgelaufen

    response = lender_client.post(
        reverse("lending:booking_receipt", args=[booking.pk]), {"art": "rueckgabe"}
    )

    assert response.status_code == 403 and mail.outbox == []


def test_ppe_expert_may_always_resend(
    client: Client, make_part: MakePart, borrower: Borrower, lender: User
) -> None:
    from tests.helpers import make_inspector

    booking = returned_by(lender, make_part, borrower)
    client.force_login(make_inspector())

    client.post(reverse("lending:booking_receipt", args=[booking.pk]), {"art": "rueckgabe"})

    assert len(mail.outbox) == 1
