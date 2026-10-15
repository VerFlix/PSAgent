"""URLs für Anmeldung, Konto und Vereinsgeräte."""

from django.urls import path

from accounts import views

app_name = "accounts"

urlpatterns = [
    path("anmelden/", views.login_view, name="login"),
    path("anmelden/passkey/optionen/", views.passkey_login_options, name="passkey_login_options"),
    path("anmelden/passkey/", views.passkey_login, name="passkey_login"),
    path("abmelden/", views.logout_view, name="logout"),
    path("konto/", views.account, name="account"),
    path(
        "konto/passkey/optionen/",
        views.passkey_register_options,
        name="passkey_register_options",
    ),
    path("konto/passkey/", views.passkey_register, name="passkey_register"),
    path("konto/passkey/<int:pk>/widerrufen/", views.passkey_revoke, name="passkey_revoke"),
    path("konto/vereinsgeraet/", views.club_device_register, name="club_device_register"),
    path(
        "konto/vereinsgeraet/<int:pk>/widerrufen/",
        views.club_device_revoke,
        name="club_device_revoke",
    ),
    path("einrichten/<str:token>/", views.enroll, name="enroll"),
    path("einrichten/<str:token>/optionen/", views.enroll_options, name="enroll_options"),
    path("einrichten/<str:token>/passkey/", views.enroll_finish, name="enroll_finish"),
]
