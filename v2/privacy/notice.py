"""Datenschutzhinweis zur Löschfrist (Art. 13 DSGVO): Ausgabe, Notfallmappe, Quittung."""

from django.conf import settings

# Hinweis an Kommentarfeldern: Freitext bleibt nach der Löschfrist erhalten (#63).
NO_NAMES = "Bitte keine Namen eintragen; Kommentare bleiben dauerhaft gespeichert."


def retention_notice() -> str:
    years = settings.PSAGENT_RETENTION_YEARS
    return (
        f"Datenschutz: Name, Kontaktdaten und Unterschrift löschen wir {years} Jahre nach Ende "
        "des Jahres der letzten Ausleihe, außer sie werden für einen konkreten Schadensfall "
        "gebraucht. Die Ausleihe selbst bleibt ohne Namen für die PSA-Dokumentation erhalten."
    )
