"""URLs des Notbetriebs."""

from django.urls import path

from emergency import views

app_name = "emergency"

urlpatterns = [
    path("", views.index, name="index"),
    path("mappe/neu/", views.create, name="create"),
    path("mappe/neueste.pdf", views.latest, name="latest"),
]
