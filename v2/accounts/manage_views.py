"""Verwaltung von Nutzern, Rollen und Sachkunde (#67). Nur mit „Nutzer verwalten“ (Admin).

Die Regeln stehen in `accounts.services`; die Ansichten zeigen Formulare und Meldungen.
Einrichtungslinks erscheinen genau einmal auf einer Seite, die nicht zwischengespeichert wird.
"""

from typing import Any

from django import forms
from django.contrib import messages
from django.db.models import Prefetch, QuerySet
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.cache import add_never_cache_headers
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from accounts import certificates, services
from accounts.confirm import requires_yubikey
from accounts.models import Passkey, Qualification, Role, SigningCertificate, User
from accounts.rights import Right, requires
from accounts.services import AccountError
from accounts.views import save_stamp


class AccountForm(forms.Form):
    username = forms.CharField(label="Benutzername", max_length=150)
    first_name = forms.CharField(label="Vorname", max_length=150)
    last_name = forms.CharField(label="Nachname", max_length=150)
    role = forms.ChoiceField(label="Rolle", choices=Role.choices, initial=Role.VIEWER)


class ReasonForm(forms.Form):
    reason = forms.CharField(label="Begründung", max_length=500)


class RoleForm(ReasonForm):
    role = forms.ChoiceField(label="Neue Rolle", choices=Role.choices)


class QualificationForm(forms.Form):
    valid_from = forms.DateField(label="gültig ab", widget=forms.DateInput(attrs={"type": "date"}))
    valid_until = forms.DateField(
        label="gültig bis einschließlich", widget=forms.DateInput(attrs={"type": "date"})
    )
    evidence = forms.CharField(
        label="Nachweis", max_length=500, help_text="z. B. Lehrgang, Anbieter und Datum"
    )


# Zertifikate und Bescheinigungen sind wenige Kilobyte groß.
MAX_CERTIFICATE_BYTES = 64 * 1024


class CertificateForm(forms.Form):
    name = forms.CharField(
        label="Bezeichnung", max_length=100, help_text="z. B. YubiKey 1, Reserve"
    )
    certificate = forms.FileField(label="Zertifikat aus Slot 9c (PEM oder DER)")
    attestation = forms.FileField(label="Herstellerbescheinigung für Slot 9c")
    intermediates = forms.FileField(label="Zwischenzertifikat(e) bis zu Yubico (Slot f9)")

    def clean(self) -> dict[str, Any]:
        data = super().clean() or {}
        for key in ("certificate", "attestation", "intermediates"):
            upload = data.get(key)
            if upload is not None and upload.size > MAX_CERTIFICATE_BYTES:
                raise forms.ValidationError("Eine Datei ist zu groß für ein Zertifikat.")
        return data


class CertificateRevokeForm(ReasonForm):
    revoked_at = forms.DateTimeField(
        label="widerrufen ab (leer: jetzt)",
        required=False,
        input_formats=["%Y-%m-%dT%H:%M"],
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}),
    )


def _actor(request: HttpRequest) -> User:
    assert isinstance(request.user, User)  # noqa: S101 – von requires() geprüft
    return request.user


def _accounts() -> QuerySet[User]:
    return User.objects.order_by(
        "-is_active", "last_name", "first_name", "username"
    ).prefetch_related(
        Prefetch(
            "passkeys", queryset=Passkey.objects.filter(revoked_at__isnull=True), to_attr="active"
        )
    )


def _link_page(request: HttpRequest, account: User, token: str) -> HttpResponse:
    link = request.build_absolute_uri(reverse("accounts:enroll", args=[token]))
    response = render(
        request,
        "accounts/manage/link.html",
        {"account": account, "link": link, "hours": services.INVITATION_HOURS},
    )
    add_never_cache_headers(response)
    return response


def _back(account: User) -> HttpResponse:
    return redirect("accounts:user_detail", pk=account.pk)


def _invalid(request: HttpRequest, form: forms.Form) -> None:
    for errors in form.errors.values():
        for error in errors:
            messages.error(request, str(error))


@requires(Right.MANAGE_USERS)
@require_GET
def user_list(request: HttpRequest) -> HttpResponse:
    return render(request, "accounts/manage/list.html", {"accounts": _accounts()})


@requires(Right.MANAGE_USERS)
@requires_yubikey
@require_http_methods(["GET", "POST"])
def user_create(request: HttpRequest) -> HttpResponse:
    form = AccountForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            account, token = services.create_account(**form.cleaned_data, actor=_actor(request))
        except AccountError as error:
            form.add_error(None, str(error))
        else:
            return _link_page(request, account, token)
    return render(request, "accounts/manage/create.html", {"form": form})


@requires(Right.MANAGE_USERS)
@require_GET
def user_detail(request: HttpRequest, pk: int) -> HttpResponse:
    account = get_object_or_404(User, pk=pk)
    context: dict[str, Any] = {
        "account": account,
        "passkeys": account.passkeys.filter(revoked_at__isnull=True).order_by("created_at"),
        "qualifications": Qualification.objects.filter(user=account)
        .select_related("revocation")
        .order_by("-valid_until"),
        "valid_qualification": account.has_valid_qualification(),
        "events": account.account_events.select_related("actor").order_by("-at", "-id"),
        "role_form": RoleForm(initial={"role": account.role}),
        "reason_form": ReasonForm(),
        "qualification_form": QualificationForm(),
        "certificates": SigningCertificate.objects.filter(user=account)
        .select_related("revocation")
        .order_by("-created_at"),
        "may_hold_certificate": account.has_role(Role.PPE_EXPERT),
        "is_self": account.pk == request.user.pk,
    }
    return render(request, "accounts/manage/detail.html", context)


@requires(Right.MANAGE_USERS)
@requires_yubikey
@require_POST
def user_role(request: HttpRequest, pk: int) -> HttpResponse:
    account = get_object_or_404(User, pk=pk)
    form = RoleForm(request.POST)
    if not form.is_valid():
        _invalid(request, form)
        return _back(account)
    try:
        services.change_role(
            account, Role(form.cleaned_data["role"]), actor=_actor(request), **_reason(form)
        )
    except AccountError as error:
        messages.error(request, str(error))
    else:
        messages.success(request, f"Rolle von {account} geändert.")
    return _back(account)


def _reason(form: forms.Form) -> dict[str, str]:
    return {"reason": form.cleaned_data["reason"]}


@requires(Right.MANAGE_USERS)
@requires_yubikey
@require_POST
def user_deactivate(request: HttpRequest, pk: int) -> HttpResponse:
    return _status(request, pk, services.deactivate, "deaktiviert")


@requires(Right.MANAGE_USERS)
@requires_yubikey
@require_POST
def user_reactivate(request: HttpRequest, pk: int) -> HttpResponse:
    return _status(request, pk, services.reactivate, "wieder aktiviert")


def _status(request: HttpRequest, pk: int, action: Any, done: str) -> HttpResponse:
    account = get_object_or_404(User, pk=pk)
    form = ReasonForm(request.POST)
    if not form.is_valid():
        _invalid(request, form)
        return _back(account)
    try:
        action(account, actor=_actor(request), **_reason(form))
    except AccountError as error:
        messages.error(request, str(error))
    else:
        messages.success(request, f"{account} {done}.")
    return _back(account)


@requires(Right.MANAGE_USERS)
@requires_yubikey
@require_POST
def user_enrollment(request: HttpRequest, pk: int) -> HttpResponse:
    account = get_object_or_404(User, pk=pk)
    try:
        token = services.issue_enrollment(account, actor=_actor(request))
    except AccountError as error:
        messages.error(request, str(error))
        return _back(account)
    return _link_page(request, account, token)


@requires(Right.MANAGE_USERS)
@requires_yubikey
@require_POST
def user_passkey_revoke(request: HttpRequest, pk: int, passkey: int) -> HttpResponse:
    account = get_object_or_404(User, pk=pk)
    entry = get_object_or_404(Passkey, pk=passkey, user=account)
    form = ReasonForm(request.POST)
    if not form.is_valid():
        _invalid(request, form)
        return _back(account)
    try:
        services.revoke_passkey(entry, actor=_actor(request), **_reason(form))
    except AccountError as error:
        messages.error(request, str(error))
    else:
        messages.success(request, f"Passkey „{entry.name}“ von {account} widerrufen.")
    return _back(account)


@requires(Right.MANAGE_USERS)
@requires_yubikey
@require_POST
def qualification_grant(request: HttpRequest, pk: int) -> HttpResponse:
    account = get_object_or_404(User, pk=pk)
    form = QualificationForm(request.POST)
    if not form.is_valid():
        _invalid(request, form)
        return _back(account)
    try:
        services.grant_qualification(account, **form.cleaned_data, actor=_actor(request))
    except AccountError as error:
        messages.error(request, str(error))
    else:
        messages.success(request, f"Sachkunde für {account} eingetragen.")
    return _back(account)


@requires(Right.MANAGE_USERS)
@requires_yubikey
@require_POST
def qualification_revoke(request: HttpRequest, pk: int, qualification: int) -> HttpResponse:
    account = get_object_or_404(User, pk=pk)
    entry = get_object_or_404(Qualification, pk=qualification, user=account)
    form = ReasonForm(request.POST)
    if not form.is_valid():
        _invalid(request, form)
        return _back(account)
    try:
        services.revoke_qualification(entry, actor=_actor(request), **_reason(form))
    except AccountError as error:
        messages.error(request, str(error))
    else:
        messages.success(request, f"Sachkunde von {account} widerrufen.")
    return _back(account)


@requires(Right.MANAGE_USERS)
@requires_yubikey
@require_POST
def user_stamp(request: HttpRequest, pk: int) -> HttpResponse:
    account = get_object_or_404(User, pk=pk)
    save_stamp(request, account)
    return _back(account)


@requires(Right.MANAGE_USERS)
@requires_yubikey
@require_POST
def certificate_register(request: HttpRequest, pk: int) -> HttpResponse:
    account = get_object_or_404(User, pk=pk)
    form = CertificateForm(request.POST, request.FILES)
    if not form.is_valid():
        _invalid(request, form)
        return _back(account)
    data = form.cleaned_data
    try:
        entry = certificates.register(
            account,
            certificate=data["certificate"].read(),
            attestation=data["attestation"].read(),
            intermediates=data["intermediates"].read(),
            name=data["name"],
            actor=_actor(request),
        )
    except AccountError as error:
        messages.error(request, str(error))
    else:
        messages.success(request, f"Signaturzertifikat „{entry.name}“ aufgenommen.")
    return _back(account)


@requires(Right.MANAGE_USERS)
@requires_yubikey
@require_POST
def certificate_revoke(request: HttpRequest, pk: int, certificate: int) -> HttpResponse:
    account = get_object_or_404(User, pk=pk)
    entry = get_object_or_404(SigningCertificate, pk=certificate, user=account)
    form = CertificateRevokeForm(request.POST)
    if not form.is_valid():
        _invalid(request, form)
        return _back(account)
    try:
        certificates.revoke(
            entry,
            actor=_actor(request),
            revoked_at=form.cleaned_data["revoked_at"],
            **_reason(form),
        )
    except AccountError as error:
        messages.error(request, str(error))
    else:
        messages.success(request, f"Signaturzertifikat „{entry.name}“ widerrufen.")
    return _back(account)
