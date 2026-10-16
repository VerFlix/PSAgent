"""URLs des Verleihs."""

from django.urls import path

from lending import views

app_name = "lending"

urlpatterns = [
    path("neu/", views.new_booking, name="new_booking"),
    path("neu/ausgabe/", views.checkout_new, name="checkout_new"),
    path("entleiher/suche/", views.borrower_search, name="borrower_search"),
    path("entleiher/<int:pk>/", views.borrower_status, name="borrower_status"),
    path("<int:pk>/", views.booking_detail, name="booking_detail"),
]
