"""Kontext für alle Seiten: Offline-Kopie der Notfallmappe auf Vereinsgeräten (Parität 22)."""

from django.http import HttpRequest

from accounts.rights import Right, has_right


def offline_binder(request: HttpRequest) -> dict[str, bool]:
    """Auf Vereinsgeräten hält der Service Worker die Mappe aktuell, sobald jemand ab Verleiher
    angemeldet ist; andere Geräte richten die Offline-Kopie nur von Hand ein."""
    automatic = getattr(request, "club_device", None) is not None and has_right(
        getattr(request, "user", None), Right.LEND
    )
    return {"offline_binder_auto": automatic}
