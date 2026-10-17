"""Ansichten des Datenschutzes: Löschsperren und Vorschau des täglichen Löschlaufs (#63)."""

from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from accounts.models import User
from accounts.rights import Right, requires
from lending.models import Borrower
from privacy import services
from privacy.forms import HoldForm
from privacy.models import Erasure, ErasureHold


def _actor(request: HttpRequest) -> User:
    assert isinstance(request.user, User)  # noqa: S101 – von requires() geprüft
    return request.user


@requires(Right.ERASURE_HOLD)
@require_GET
def index(request: HttpRequest) -> HttpResponse:
    """Geltende Löschsperren, was der nächste Lauf löscht, und die letzten Löschungen."""
    latest: dict[int, ErasureHold] = {}
    for hold in ErasureHold.objects.select_related("borrower", "actor"):
        latest.setdefault(hold.borrower_id, hold)
    context = {
        "holds": [hold for hold in latest.values() if hold.active],
        "due": services.due_borrowers(services.today()),
        "erasures": Erasure.objects.select_related("actor")[:20],
        "form": HoldForm(),
    }
    return render(request, "privacy/index.html", context)


@requires(Right.ERASURE_HOLD)
@require_POST
def hold(request: HttpRequest, pk: int) -> HttpResponse:
    """Löschsperre setzen (aus der Detailansicht eines Ausleihvorgangs)."""
    return _change(request, pk, set_hold=True)


@requires(Right.ERASURE_HOLD)
@require_POST
def release(request: HttpRequest, pk: int) -> HttpResponse:
    """Löschsperre aufheben."""
    return _change(request, pk, set_hold=False)


def _change(request: HttpRequest, pk: int, *, set_hold: bool) -> HttpResponse:
    borrower = get_object_or_404(Borrower, pk=pk)
    form = HoldForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Bitte einen Grund angeben.")
        return redirect("privacy:index")
    change = services.set_hold if set_hold else services.release_hold
    try:
        change(borrower, form.cleaned_data["reason"], actor=_actor(request))
    except services.PrivacyError as error:
        messages.error(request, str(error))
    else:
        state = "gesetzt" if set_hold else "aufgehoben"
        messages.success(request, f"Löschsperre für Entleiher #{borrower.pk} {state}.")
    return redirect("privacy:index")
