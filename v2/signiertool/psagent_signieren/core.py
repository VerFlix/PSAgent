"""Prüfprotokoll lesen, zusammenfassen und signieren (#38 Schritt 3).

Der Kern kennt nur einen pyHanko-`Signer`: im Betrieb den YubiKey über PKCS#11
(`psagent_signieren.yubikey`), in Tests einen Software-Schlüssel. Alles Kryptografische
übernimmt pyHanko (CLAUDE.md, Regel 6).

Signiert wird als Nachtrag in das vorbereitete Feld „Sachkundiger“, sonst ändert sich nichts.
So nimmt der Server das PDF an (Byte-Vergleich und Differenzprüfung, `inspections.signature`).
Das Feld zeigt die hinterlegte Unterschrift als Bild mit Name, Datum und „digital signiert“;
gültig ist allein die digitale Signatur (Konzept, „Sichtbare Unterschrift“).
"""

import hashlib
import re
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from pyhanko import stamp
from pyhanko.pdf_utils import images, layout, text
from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
from pyhanko.pdf_utils.reader import PdfFileReader
from pyhanko.sign import fields, signers
from pypdf import PdfReader

# Wie `inspections.protocol.SIGNATURE_FIELD` im Server.
SIGNATURE_FIELD = "Sachkundiger"
STAMP_TEXT = "%(signer)s\nDigital signiert am %(ts)s\nGültig ist die digitale Signatur."


class SigningError(Exception):
    """Das PDF lässt sich nicht signieren; der Text sagt, warum."""


@dataclass(frozen=True)
class Summary:
    """Was vor dem Signieren angezeigt wird."""

    title: str
    pages: int
    round: str
    parts: str
    chain_head: str
    sha256: str

    def lines(self) -> list[str]:
        return [
            f"Dokument:      {self.title}",
            f"Seiten:        {self.pages}",
            f"Runde:         {self.round}",
            f"Geprüfte Teile: {self.parts}",
            f"Kettenkopf:    {self.chain_head}",
            f"SHA-256:       {self.sha256}",
        ]


def _find(pattern: str, text: str) -> str:
    match = re.search(pattern, text)
    return match.group(1).strip() if match else "–"


def summarize(pdf: bytes) -> Summary:
    """Prüft, dass es ein unsigniertes Prüfprotokoll ist, und fasst es zusammen."""
    try:
        reader = PdfFileReader(BytesIO(pdf))
        empty = [name for name, value, _ in fields.enumerate_sig_fields(reader) if value is None]
        signed = reader.embedded_signatures
        document = PdfReader(BytesIO(pdf))
        text = " ".join(page.extract_text() for page in document.pages)
        pages = len(document.pages)
        title = (document.metadata.title if document.metadata else None) or "–"
    except Exception as error:
        raise SigningError("Die Datei ist kein lesbares PDF.") from error
    if signed:
        raise SigningError("Das PDF ist bereits signiert.")
    if empty != [SIGNATURE_FIELD]:
        raise SigningError(
            f"Kein PSAgent-Prüfprotokoll: Das leere Signaturfeld „{SIGNATURE_FIELD}“ fehlt."
        )
    text = re.sub(r"\s+", " ", text)
    return Summary(
        title=title,
        pages=pages,
        round=_find(r"Prüfungsrunde:\s*(\d+)", text),
        parts=_find(r"Geprüfte Teile \((\d+)\)", text),
        chain_head=_find(r"Kettenkopf Nummer:\s*(\d+)", text),
        sha256=hashlib.sha256(pdf).hexdigest(),
    )


def stamp_style(image: Path | None) -> stamp.TextStampStyle:
    """Sichtbares Feld: oben die Unterschrift als Bild (falls hinterlegt), unten Name und Datum."""
    background = images.PdfImage(str(image)) if image is not None else None
    return stamp.TextStampStyle(
        stamp_text=STAMP_TEXT,
        timestamp_format="%d.%m.%Y %H:%M",
        background=background,
        background_opacity=1.0,
        background_layout=layout.SimpleBoxLayoutRule(
            x_align=layout.AxisAlignment.ALIGN_MIN,
            y_align=layout.AxisAlignment.ALIGN_MAX,
            margins=layout.Margins(left=6, right=6, top=4, bottom=34),
        ),
        inner_content_layout=layout.SimpleBoxLayoutRule(
            x_align=layout.AxisAlignment.ALIGN_MIN,
            y_align=layout.AxisAlignment.ALIGN_MIN,
            margins=layout.Margins(left=6, bottom=4),
        ),
        text_box_style=text.TextBoxStyle(font_size=7),
        border_width=0,
    )


def sign(pdf: bytes, signer: signers.Signer, *, image: Path | None = None) -> bytes:
    """Signiert in das Feld „Sachkundiger“ (PAdES) und gibt das neue PDF zurück."""
    summarize(pdf)
    meta = signers.PdfSignatureMetadata(
        field_name=SIGNATURE_FIELD,
        md_algorithm="sha256",
        subfilter=fields.SigSeedSubFilter.PADES,
    )
    pdf_signer = signers.PdfSigner(meta, signer, stamp_style=stamp_style(image))
    output = pdf_signer.sign_pdf(IncrementalPdfFileWriter(BytesIO(pdf)), existing_fields_only=True)
    signed: bytes = output.getvalue()
    return signed


def output_path(source: Path) -> Path:
    """`protokoll.pdf` → `protokoll_signiert.pdf`; vorhandene Dateien werden nie überschrieben."""
    target = source.with_name(f"{source.stem}_signiert.pdf")
    if target.exists():
        raise SigningError(f"{target.name} gibt es schon. Bitte erst umbenennen oder löschen.")
    return target
