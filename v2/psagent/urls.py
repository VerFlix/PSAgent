"""URL-Konfiguration von PSAgent 2.0."""

from django.urls import include, path

from psagent import views

urlpatterns = [
    path("", views.home, name="home"),
    path("", include("accounts.urls")),
    path("healthz", views.healthz, name="healthz"),
]
