"""URL-Konfiguration von PSAgent 2.0."""

from django.urls import path

from psagent import views

urlpatterns = [
    path("healthz", views.healthz, name="healthz"),
]
