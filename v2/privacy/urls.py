"""URLs des Datenschutzes."""

from django.urls import path

from privacy import views

app_name = "privacy"

urlpatterns = [
    path("", views.index, name="index"),
    path("entleiher/<int:pk>/sperre/", views.hold, name="hold"),
    path("entleiher/<int:pk>/sperre-aufheben/", views.release, name="release"),
]
