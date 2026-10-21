"""URLs der PSA-Prüfung."""

from django.urls import path

from inspections import round_views, views

app_name = "inspections"

urlpatterns = [
    path("naechste/", views.next_due, name="next_due"),
    path("runde/", round_views.round_mode, name="round_mode"),
    path("runde/beginnen/", round_views.round_start, name="round_start"),
    path("runde/teil/<int:part_pk>/", round_views.part_to_round, name="part_to_round"),
    path("runde/<int:pk>/scan/", round_views.round_scan, name="round_scan"),
    path(
        "runde/<int:pk>/teil/<int:part_pk>/ergebnis/",
        round_views.round_result,
        name="round_result",
    ),
    path("runde/<int:pk>/teil/<int:part_pk>/entfernen/", round_views.round_drop, name="round_drop"),
    path("runde/<int:pk>/abschliessen/", round_views.round_close, name="round_close"),
    path("runde/<int:pk>/abbrechen/", round_views.round_cancel, name="round_cancel"),
    path("runde/<int:pk>/protokoll/", round_views.round_protocol, name="round_protocol"),
]
