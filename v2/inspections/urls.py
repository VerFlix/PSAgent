"""URLs der PSA-Prüfung."""

from django.urls import path

from inspections import views

app_name = "inspections"

urlpatterns = [
    path("naechste/", views.next_due, name="next_due"),
]
