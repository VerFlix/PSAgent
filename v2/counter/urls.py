"""URLs der Theke (#127)."""

from django.urls import path

from counter import returns, settings_views, today, views

app_name = "counter"

urlpatterns = [
    path("", views.home, name="home"),
    path("ausgabe/neu/", views.checkout_new, name="checkout_new"),
    path("ausgabe/", views.checkout_period, name="checkout_period"),
    path("ausgabe/teile/", views.checkout_parts, name="checkout_parts"),
    path("ausgabe/teile/vorschlaege/", views.part_suggestions, name="part_suggestions"),
    path("ausgabe/entleiher/", views.checkout_borrower, name="checkout_borrower"),
    path("ausgabe/entleiher/suche/", views.borrower_search, name="borrower_search"),
    path("ausgabe/unterschrift/", views.checkout_sign, name="checkout_sign"),
    path("ausgabe/reservierungen/", views.checkout_reservations, name="checkout_reservations"),
    path("rueckgabe/", returns.returns, name="returns"),
    path("rueckgabe/<int:pk>/", returns.return_booking, name="return_booking"),
    path("schaden/", returns.damage_report, name="damage_report"),
    path("heute/", today.today_view, name="today"),
    path("einstellungen/", settings_views.counter_settings, name="settings"),
]
