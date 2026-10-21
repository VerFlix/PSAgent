"""YubiKey über PKCS#11 (Yubico PIV Tool, `libykcs11`), Slot 9c „Digital Signature“.

Die PIN fragt das Tool bei jeder Signatur ab und gibt sie nur an das PKCS#11-Modul weiter; sie
wird nie gespeichert. Zusätzlich verlangt der YubiKey eine Berührung (Richtlinie beim Erzeugen
des Schlüssels, siehe Runbook).
"""

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pkcs11
from pkcs11 import TokenFlag
from pyhanko.config.pkcs11 import TokenCriteria
from pyhanko.sign.pkcs11 import PKCS11Signer, open_pkcs11_session

from psagent_signieren.core import SigningError

# Beschriftung der Objekte für Slot 9c in `libykcs11`.
CERT_LABEL = "X.509 Certificate for Digital Signature"
KEY_LABEL = "Private key for Digital Signature"
WINDOWS_MODULE = r"C:\Program Files\Yubico\Yubico PIV Tool\bin\libykcs11.dll"


def module_path(explicit: str | None = None) -> str:
    """Pfad zum PKCS#11-Modul: Option, sonst `PSAGENT_PKCS11_MODUL`, sonst der Standardort."""
    path = explicit or os.environ.get("PSAGENT_PKCS11_MODUL", "")
    if not path:
        path = WINDOWS_MODULE if os.name == "nt" else "libykcs11.so"
    if os.sep in path and not Path(path).exists():
        raise SigningError(
            f"PKCS#11-Modul nicht gefunden: {path}. Ist das Yubico PIV Tool installiert?"
        )
    return path


def token_label(module: str) -> str:
    """Bezeichnung des einen eingerichteten Tokens (YubiKey) am Modul."""
    try:
        slots = pkcs11.lib(module).get_slots(token_present=True)
        tokens = [slot.get_token() for slot in slots]
    except Exception as error:
        raise SigningError(f"PKCS#11-Modul lässt sich nicht laden: {module}") from error
    ready = [token for token in tokens if token.flags & TokenFlag.TOKEN_INITIALIZED]
    if not ready:
        raise SigningError("Kein YubiKey gefunden. Bitte den YubiKey anstecken.")
    if len(ready) > 1:
        raise SigningError("Mehrere YubiKeys gefunden. Bitte nur einen anstecken.")
    return str(ready[0].label)


@contextmanager
def signer(
    module: str, pin: str, *, cert_label: str = CERT_LABEL, key_label: str = KEY_LABEL
) -> Iterator[PKCS11Signer]:
    """Signierer für den YubiKey am Rechner; schließt die Sitzung danach."""
    label = token_label(module)
    try:
        session = open_pkcs11_session(
            module, token_criteria=TokenCriteria(label=label), user_pin=pin
        )
    except Exception as error:
        raise SigningError(
            "YubiKey nicht erreichbar oder PIN falsch. Steckt der YubiKey?"
        ) from error
    try:
        # Hash am Rechner, im Token nur die Signatur (CKM_ECDSA): kann jedes Modul, auch
        # SoftHSM in den Tests; der Schlüssel verlässt den YubiKey dabei nie.
        yield PKCS11Signer(
            session, cert_label=cert_label, key_label=key_label, use_raw_mechanism=True
        )
    finally:
        session.close()
