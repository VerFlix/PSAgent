"""URL-Konfiguration von PSAgent 2.0."""

from django.urls import include, path

from inventory import views as inventory_views
from psagent import views

urlpatterns = [
    path("", views.home, name="home"),
    path("", include("accounts.urls")),
    path("teile/", include("inventory.urls")),
    # Ziel der QR-Etiketten (#14); kurz, damit der QR-Code klein bleibt.
    path("q/<path:ei>", inventory_views.scan_target, name="scan_target"),
    path("verleih/", include("lending.urls")),
    path("pruefung/", include("inspections.urls")),
    path("notbetrieb/", include("emergency.urls")),
    path("datenschutz/", include("privacy.urls")),
    path("healthz", views.healthz, name="healthz"),
    path("verwaltung/sicherung/", views.backup_view, name="backup"),
]
