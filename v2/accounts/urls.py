"""URLs für Anmeldung, Konto, Vereinsgeräte und die Verwaltung von Nutzern."""

from django.urls import path

from accounts import manage_views, views

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
    path("einrichten/<str:token>/passwort/", views.enroll_password, name="enroll_password"),
    # Verwaltung (nur Admin)
    path("verwaltung/nutzer/", manage_views.user_list, name="users"),
    path("verwaltung/nutzer/neu/", manage_views.user_create, name="user_create"),
    path("verwaltung/nutzer/<int:pk>/", manage_views.user_detail, name="user_detail"),
    path("verwaltung/nutzer/<int:pk>/rolle/", manage_views.user_role, name="user_role"),
    path(
        "verwaltung/nutzer/<int:pk>/deaktivieren/",
        manage_views.user_deactivate,
        name="user_deactivate",
    ),
    path(
        "verwaltung/nutzer/<int:pk>/aktivieren/",
        manage_views.user_reactivate,
        name="user_reactivate",
    ),
    path(
        "verwaltung/nutzer/<int:pk>/einrichtungslink/",
        manage_views.user_enrollment,
        name="user_enrollment",
    ),
    path(
        "verwaltung/nutzer/<int:pk>/passkey/<int:passkey>/widerrufen/",
        manage_views.user_passkey_revoke,
        name="user_passkey_revoke",
    ),
    path(
        "verwaltung/nutzer/<int:pk>/sachkunde/",
        manage_views.qualification_grant,
        name="qualification_grant",
    ),
    path(
        "verwaltung/nutzer/<int:pk>/sachkunde/<int:qualification>/widerrufen/",
        manage_views.qualification_revoke,
        name="qualification_revoke",
    ),
]
