"""URLs des Notbetriebs."""

from django.urls import path

from emergency import views

app_name = "emergency"

urlpatterns = [
    path("", views.index, name="index"),
    path("mappe/neu/", views.create, name="create"),
    path("mappe/neueste.pdf", views.latest, name="latest"),
    path("nacherfassen/sperrungen/", views.backfill_locks, name="backfill_locks"),
    path("nacherfassen/ausgaben/", views.backfill_loans, name="backfill_loans"),
    path("klaerung/", views.clarifications, name="clarifications"),
    path(
        "klaerung/ausgabe/<int:pk>/uebernehmen/",
        views.apply_loan_form,
        name="apply_loan_form",
    ),
    path("formular/<int:pk>/scan.pdf", views.scan, name="scan"),
]
