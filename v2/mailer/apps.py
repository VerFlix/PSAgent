"""App-Konfiguration für den E-Mail-Versand."""

from django.apps import AppConfig


class MailerConfig(AppConfig):
    """Versand über Microsoft 365 (Graph) und Mailvorlagen (#72)."""

    name = "mailer"
    verbose_name = "E-Mail"
