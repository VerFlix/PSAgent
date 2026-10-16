"""URLs der Stammdaten (Teile)."""

from django.urls import path

from inventory import views

app_name = "inventory"

urlpatterns = [
    path("", views.part_list, name="part_list"),
    path("neu/produkt/", views.product_create, name="product_create"),
    path("neu/system/", views.system_create, name="system_create"),
    path("neu/system/bauteilzeile/", views.system_component_row, name="system_component_row"),
    path("gal-zuweisen/", views.gal_assign, name="gal_assign"),
    path("gal/<int:pk>/", views.document_open, name="document_open"),
    path("<int:pk>/", views.part_detail, name="part_detail"),
    path("<int:pk>/bearbeiten/", views.part_edit, name="part_edit"),
    path("<int:pk>/sperren/", views.part_lock, name="part_lock"),
    path("<int:pk>/entsperren/", views.part_unlock, name="part_unlock"),
]
