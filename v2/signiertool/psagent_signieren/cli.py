"""Kommandozeile des Signier-Tools.

    signieren unterschrift BILD     Unterschrift als Bild einmal hinterlegen (Benutzerordner)
    signieren PROTOKOLL.pdf          anzeigen, bestätigen, mit dem YubiKey signieren
    signieren pruefen BERICHT.pdf    signierten Prüfbericht ohne Datenbank prüfen (#19)

Das signierte PDF heißt `<name>_signiert.pdf` und liegt neben dem Original; hochgeladen wird es
im Prüfmodus von PSAgent. Das Tool braucht keine Zugangsdaten zum Server.
"""

import argparse
import getpass
import logging
import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from PIL import Image

from psagent_signieren import check, core, yubikey

# Rückgabewerte von `signieren pruefen`, wie bei `manage.py kette_pruefen`.
EXIT = {check.UNCHANGED: 0, check.CHANGED: 1, check.UNCHECKABLE: 2}


def settings_folder() -> Path:
    """Ordner für die Unterschrift: `PSAGENT_SIGNIEREN_ORDNER`, sonst der Benutzerordner."""
    explicit = os.environ.get("PSAGENT_SIGNIEREN_ORDNER", "")
    if explicit:
        return Path(explicit)
    if os.name == "nt" and os.environ.get("APPDATA"):
        return Path(os.environ["APPDATA"]) / "PSAgent"
    return Path.home() / ".config" / "psagent"


def signature_image() -> Path | None:
    path = settings_folder() / "unterschrift.png"
    return path if path.exists() else None


def store_image(source: Path) -> Path:
    """Prüft das Bild und legt es als PNG im Benutzerordner ab."""
    try:
        with Image.open(source) as image:
            image.load()
            converted = image.convert("RGBA")
    except Exception as error:
        raise core.SigningError(f"{source} ist kein lesbares Bild.") from error
    folder = settings_folder()
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / "unterschrift.png"
    converted.save(target, format="PNG")
    return target


def show(path: Path) -> None:
    """Öffnet das PDF im Standardbetrachter, damit man sieht, was man signiert."""
    if os.name == "nt":
        os.startfile(path)  # type: ignore[attr-defined]  # noqa: S606 – nur unter Windows
    elif shutil.which("xdg-open"):
        subprocess.Popen(["xdg-open", str(path)])  # noqa: S603, S607 – fester Befehl
    elif shutil.which("open"):
        subprocess.Popen(["open", str(path)])  # noqa: S603, S607 – fester Befehl


def run_sign(
    args: argparse.Namespace,
    *,
    ask: Callable[[str], str] | None = None,
    ask_pin: Callable[[str], str] | None = None,
) -> Path:
    ask = ask or input
    ask_pin = ask_pin or getpass.getpass
    source = Path(args.pdf)
    try:
        pdf = source.read_bytes()
    except OSError as error:
        raise core.SigningError(f"{source} lässt sich nicht lesen.") from error
    summary = core.summarize(pdf)
    target = core.output_path(source)
    print("\n".join(summary.lines()))
    if not args.ohne_anzeige:
        show(source)
    image = signature_image()
    print(f"Unterschrift als Bild: {image or 'keine hinterlegt (nur Text im Feld)'}")
    if not args.ja and ask("Jetzt signieren? (j/n) ").strip().lower() not in ("j", "ja"):
        raise core.SigningError("Abgebrochen, nichts signiert.")
    module = yubikey.module_path(args.modul)
    pin = ask_pin("PIN des YubiKeys: ")
    print("Bitte den YubiKey berühren, sobald er blinkt …")
    with yubikey.signer(
        module, pin, cert_label=args.zertifikat, key_label=args.schluessel
    ) as signer:
        signed = core.sign(pdf, signer, image=image)
    target.write_bytes(signed)
    print(f"Signiert: {target}")
    print("Jetzt in PSAgent im Prüfmodus hochladen.")
    return target


def run_check(args: argparse.Namespace, write: Callable[[str], None]) -> int:
    """Prüft einen signierten Prüfbericht; Rückgabewert 0, 1 oder 2."""
    # pyHanko meldet jeden Befund zusätzlich mit Traceback; das Ergebnis unten sagt es verständlich.
    for name in ("pyhanko", "pyhanko_certvalidator"):
        logging.getLogger(name).setLevel(logging.CRITICAL)
    try:
        roots = check.read_tsa_roots(Path(args.tsa_ca)) if args.tsa_ca else None
        listed = (
            check.read_fingerprints(Path(args.fingerabdruecke)) if args.fingerabdruecke else None
        )
        pdf = Path(args.pdf).read_bytes()
    except (check.InputError, OSError) as error:
        write(f"Fehler: {error}")
        return EXIT[check.UNCHECKABLE]
    result = check.check(pdf, tsa_roots=roots, fingerprints=listed)
    for title, lines in (
        ("Verändert", result.findings),
        ("Nicht prüfbar", result.unchecked),
        ("Geprüft", result.confirmed),
        ("Angaben im Bericht", result.info),
    ):
        if lines:
            write(f"{title}:")
            write("\n".join(f"  {line}" for line in lines))
    write(f"Ergebnis: {result.verdict}.")
    return EXIT[result.verdict]


def main(argv: Sequence[str] | None = None, *, write: Callable[[str], None] = print) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments[:1] == ["pruefen"]:
        parser = argparse.ArgumentParser(
            prog="signieren pruefen",
            description="Signierten PSAgent-Prüfbericht ohne Datenbank prüfen.",
        )
        parser.add_argument("pdf", help="signierter Prüfbericht (Seite „Prüfberichte“)")
        parser.add_argument("--tsa-ca", help="Zertifizierungsstelle des Zeitstempeldienstes (PEM)")
        parser.add_argument("--fingerabdruecke", help="Liste der Fingerabdrücke (Textdatei)")
        return run_check(parser.parse_args(arguments[1:]), write)
    try:
        if arguments[:1] == ["unterschrift"]:
            parser = argparse.ArgumentParser(prog="signieren unterschrift")
            parser.add_argument("bild", help="Bild der Unterschrift (PNG oder JPG)")
            target = store_image(Path(parser.parse_args(arguments[1:]).bild))
            print(f"Unterschrift hinterlegt: {target}")
            return 0
        parser = argparse.ArgumentParser(
            prog="signieren", description="PSAgent-Prüfprotokoll mit dem YubiKey signieren."
        )
        parser.add_argument("pdf", help="heruntergeladenes Prüfprotokoll")
        parser.add_argument("--modul", help="Pfad zu libykcs11 (Yubico PIV Tool)")
        parser.add_argument("--ohne-anzeige", action="store_true", help="PDF nicht öffnen")
        parser.add_argument("--ja", action="store_true", help="ohne Rückfrage signieren")
        parser.add_argument("--zertifikat", default=yubikey.CERT_LABEL, help=argparse.SUPPRESS)
        parser.add_argument("--schluessel", default=yubikey.KEY_LABEL, help=argparse.SUPPRESS)
        run_sign(parser.parse_args(arguments))
    except core.SigningError as error:
        print(f"Fehler: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
