"""URLs des Verleihs."""

from django.urls import path

from lending import overview, views

app_name = "lending"

urlpatterns = [
    path("", overview.overview, name="overview"),
    path("kalender/", overview.calendar_view, name="calendar"),
    path("neu/", views.new_booking, name="new_booking"),
    path("neu/ausgabe/", views.checkout_new, name="checkout_new"),
    path("entleiher/suche/", views.borrower_search, name="borrower_search"),
    path("entleiher/<int:pk>/", views.borrower_status, name="borrower_status"),
    path("<int:pk>/", views.booking_detail, name="booking_detail"),
    path("<int:pk>/ausgabe/", views.booking_checkout, name="booking_checkout"),
    path("<int:pk>/rueckgabe/", views.booking_return, name="booking_return"),
    path("<int:pk>/stornieren/", views.booking_cancel, name="booking_cancel"),
]
