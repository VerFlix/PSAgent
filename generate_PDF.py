"""
Funktionen um Einsatzdokumentation.pdf, Haftungsausschluss.pdf und PD.pdf 
aus typst Vorlage zu generieren

Voraussetzung: Typst muss installiert sein

Windows Installation:
1. Herunterladen von: https://github.com/typst/typst/releases
   - Datei: typst-x86_64-pc-windows-msvc.zip
   - Entpacken nach C:\\Program Files (x86)\\typst-x86_64-pc-windows-msvc\\
   - Optional: Zum PATH hinzufügen
2. Via Scoop: scoop install typst
3. Via Chocolatey: choco install typst
4. Via winget: winget install --id Typst.Typst

Linux Installation:
- Via Package Manager: apt install typst / dnf install typst / pacman -S typst
- Via Cargo: cargo install --git https://github.com/typst/typst
"""
import sqlite3
import subprocess
import shutil
from pathlib import Path

DEFAULT_DB_PATH = Path("psa.db")


def find_typst_executable() -> str | None:
    """
    Findet den Pfad zur typst executable.
    
    Returns:
        Pfad zu typst oder None
    """
    # Zuerst im PATH suchen (funktioniert auf allen Systemen)
    try:
        typst_path = shutil.which("typst")
        if typst_path:
            return typst_path
    except Exception:
        pass
    
    # In bekannten Installationsverzeichnissen suchen
    possible_locations = [
        # Linux Standard-Pfade
        Path("/usr/bin/typst"),
        Path("/usr/local/bin/typst"),
        Path.home() / ".cargo" / "bin" / "typst",
        # Windows Pfade
        Path(r"C:\Program Files (x86)\typst-x86_64-pc-windows-msvc\typst.exe"),
        Path(r"C:\Program Files\typst-x86_64-pc-windows-msvc\typst.exe"),
        Path.home() / "typst" / "typst.exe",
        Path.home() / "AppData" / "Local" / "Programs" / "typst" / "typst.exe",
    ]
    
    for location in possible_locations:
        try:
            if location.exists():
                return str(location)
        except Exception:
            continue
    
    return None


def check_typst_installed() -> bool:
    """
    Prüft, ob Typst installiert ist.
    
    Returns:
        True wenn Typst verfügbar ist, sonst False
    """
    return find_typst_executable() is not None


def init_db(db_path: Path = DEFAULT_DB_PATH) -> None:
    """
    Erstellt (falls nötig) die SQLite-Datenbank und Tabellen.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                produktbezeichnung TEXT,
                gem_en TEXT,
                produktname TEXT,
                hersteller TEXT,
                herstellungsjahr TEXT,
                kaufdatum TEXT,
                datum_einsatz TEXT,
                einzelidentifikation TEXT,
                seriennummer TEXT,
                gal_datei TEXT,
                gal_link TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS systems (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT,
                gal_datei TEXT,
                gal_link TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS system_parts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                system_id INTEGER NOT NULL,
                part_index INTEGER NOT NULL,
                produktbezeichnung TEXT,
                gem_en TEXT,
                produktname TEXT,
                hersteller TEXT,
                herstellungsjahr TEXT,
                kaufdatum TEXT,
                datum_einsatz TEXT,
                einzelidentifikation TEXT,
                seriennummer TEXT,
                FOREIGN KEY(system_id) REFERENCES systems(id)
            )
            """
        )


def fetch_product(db_path: Path, product_id: int) -> dict:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT produktbezeichnung, gem_en, produktname, hersteller,
                   herstellungsjahr, kaufdatum, datum_einsatz,
                   einzelidentifikation, seriennummer
            FROM products
            WHERE id = ?
            """,
            (product_id,),
        ).fetchone()
    return dict(row) if row else {}


def fetch_system(db_path: Path, system_id: int) -> dict:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT id, name, gal_datei, gal_link
            FROM systems
            WHERE id = ?
            """,
            (system_id,),
        ).fetchone()
    return dict(row) if row else {}


def fetch_system_parts(db_path: Path, system_id: int) -> list[dict]:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT part_index, produktbezeichnung, gem_en, produktname, hersteller,
                   herstellungsjahr, kaufdatum, datum_einsatz,
                   einzelidentifikation, seriennummer
            FROM system_parts
            WHERE system_id = ?
            ORDER BY part_index ASC
            """,
            (system_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def build_inputs_from_product(product: dict) -> dict[str, str]:
    return {
        "t1_Produktbezeichnung": product.get("produktbezeichnung", ""),
        "t1_gem_EN": product.get("gem_en", ""),
        "t1_Produktname": product.get("produktname", ""),
        "t1_Hersteller": product.get("hersteller", ""),
        "t1_Herstellungsjahr": product.get("herstellungsjahr", ""),
        "t1_Kaufdatum": product.get("kaufdatum", ""),
        "t1_Datum_Einsatz": product.get("datum_einsatz", ""),
        "t1_Einzelidentifikation": product.get("einzelidentifikation", ""),
        "t1_Seriennummer": product.get("seriennummer", ""),
    }


def build_inputs_from_system_parts(parts: list[dict]) -> dict[str, str]:
    inputs: dict[str, str] = {}
    for part in parts:
        index = part.get("part_index")
        if index not in (1, 2, 3):
            continue
        prefix = f"t{index}_"
        inputs.update(
            {
                f"{prefix}Produktbezeichnung": part.get("produktbezeichnung", ""),
                f"{prefix}gem_EN": part.get("gem_en", ""),
                f"{prefix}Produktname": part.get("produktname", ""),
                f"{prefix}Hersteller": part.get("hersteller", ""),
                f"{prefix}Herstellungsjahr": part.get("herstellungsjahr", ""),
                f"{prefix}Kaufdatum": part.get("kaufdatum", ""),
                f"{prefix}Datum_Einsatz": part.get("datum_einsatz", ""),
                f"{prefix}Einzelidentifikation": part.get("einzelidentifikation", ""),
                f"{prefix}Seriennummer": part.get("seriennummer", ""),
            }
        )
    return inputs


def build_inputs_from_db(
    product_id: int | None = None,
    system_id: int | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> dict[str, str]:
    inputs: dict[str, str] = {}
    if product_id is not None:
        product = fetch_product(db_path, product_id)
        inputs.update(build_inputs_from_product(product))
    if system_id is not None:
        parts = fetch_system_parts(db_path, system_id)
        inputs.update(build_inputs_from_system_parts(parts))
    return inputs


def _build_typst_command(
    typst_exe: str,
    template_path: Path,
    output_path: Path,
    inputs: dict[str, str] | None = None,
) -> list[str]:
    cmd = [typst_exe, "compile", str(template_path), str(output_path)]
    if inputs:
        for key, value in inputs.items():
            cmd.extend(["--input", f"{key}={value}"])
    return cmd


def generate_einsatzdokumentation_pdf(
    output_dir: str = "PDF-Dokumente",
    inputs: dict[str, str] | None = None,
    system_name: str | None = None,
) -> Path:
    """
    Generiert Einsatzdokumentation.pdf aus der Typst-Vorlage.
    
    Args:
        output_dir: Ausgabeverzeichnis für die PDF-Datei
        inputs: Optional dictionary mit Typst-Eingabevariablen
        system_name: Optional name des Systems für Dateinamen
        
    Returns:
        Path zum generierten PDF
    """
    typst_exe = find_typst_executable()
    if not typst_exe:
        raise RuntimeError("Typst wurde nicht gefunden.")
    template_path = Path("Typst_Vorlagen/Einsatzdokumentation.typ")
    
    # Verwende Systemname oder Einzelidentifikation als Präfix
    prefix = ""
    if system_name:
        prefix = system_name + "_"
    elif inputs and "t1_Einzelidentifikation" in inputs:
        prefix = inputs["t1_Einzelidentifikation"] + "_"
    
    # Füge systemname zu inputs hinzu wenn vorhanden
    if inputs is None:
        inputs = {}
    if system_name:
        inputs = {**inputs, "systemname": system_name}
    
    output_path = Path(output_dir) / f"{prefix}Einsatzdokumentation.pdf"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    result = subprocess.run(
        _build_typst_command(typst_exe, template_path, output_path, inputs),
        capture_output=True,
        text=True
    )
    
    if result.returncode != 0:
        raise RuntimeError(f"Fehler beim Generieren der PDF: {result.stderr}")
    
    print(f"✓ Einsatzdokumentation.pdf erfolgreich erstellt: {output_path}")
    return output_path


def generate_haftungsausschluss_pdf(
    output_dir: str = "PDF-Dokumente",
    inputs: dict[str, str] | None = None,
    system_name: str | None = None,
) -> Path:
    """
    Generiert Haftungsausschluss.pdf aus der Typst-Vorlage.
    
    Args:
        output_dir: Ausgabeverzeichnis für die PDF-Datei
        inputs: Optional dictionary mit Typst-Eingabevariablen
        system_name: Optional name des Systems für Dateinamen
        
    Returns:
        Path zum generierten PDF
    """
    typst_exe = find_typst_executable()
    if not typst_exe:
        raise RuntimeError("Typst wurde nicht gefunden.")
    template_path = Path("Typst_Vorlagen/Haftungsausschluss.typ")
    
    # Verwende Systemname oder Einzelidentifikation als Präfix
    prefix = ""
    if system_name:
        prefix = system_name + "_"
    elif inputs and "t1_Einzelidentifikation" in inputs:
        prefix = inputs["t1_Einzelidentifikation"] + "_"
    
    # Füge systemname zu inputs hinzu wenn vorhanden
    if inputs is None:
        inputs = {}
    if system_name:
        inputs = {**inputs, "systemname": system_name}
    
    output_path = Path(output_dir) / f"{prefix}Haftungsausschluss.pdf"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    result = subprocess.run(
        _build_typst_command(typst_exe, template_path, output_path, inputs),
        capture_output=True,
        text=True
    )
    
    if result.returncode != 0:
        raise RuntimeError(f"Fehler beim Generieren der PDF: {result.stderr}")
    
    print(f"✓ Haftungsausschluss.pdf erfolgreich erstellt: {output_path}")
    return output_path


def generate_pd_pdf(
    output_dir: str = "PDF-Dokumente",
    inputs: dict[str, str] | None = None,
    system_name: str | None = None,
) -> Path:
    """
    Generiert PD.pdf (Produktdatenblatt) aus der Typst-Vorlage.
    
    Args:
        output_dir: Ausgabeverzeichnis für die PDF-Datei
        inputs: Optional dictionary mit Typst-Eingabevariablen
        system_name: Optional name des Systems für Dateinamen
        
    Returns:
        Path zum generierten PDF
    """
    typst_exe = find_typst_executable()
    if not typst_exe:
        raise RuntimeError("Typst wurde nicht gefunden.")
    template_path = Path("Typst_Vorlagen/PD.typ")
    
    # Verwende Systemname oder Einzelidentifikation als Präfix
    prefix = ""
    if system_name:
        prefix = system_name + "_"
    elif inputs and "t1_Einzelidentifikation" in inputs:
        prefix = inputs["t1_Einzelidentifikation"] + "_"
    
    # Füge systemname zu inputs hinzu wenn vorhanden
    if inputs is None:
        inputs = {}
    if system_name:
        inputs = {**inputs, "systemname": system_name}
    
    output_path = Path(output_dir) / f"{prefix}PD.pdf"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    result = subprocess.run(
        _build_typst_command(typst_exe, template_path, output_path, inputs),
        capture_output=True,
        text=True
    )
    
    if result.returncode != 0:
        raise RuntimeError(f"Fehler beim Generieren der PDF: {result.stderr}")
    
    print(f"✓ PD.pdf erfolgreich erstellt: {output_path}")
    return output_path


def generate_all_pdfs(output_dir: str = "PDF-Dokumente") -> list[Path]:
    """
    Generiert alle drei PDF-Dateien.
    
    Args:
        output_dir: Ausgabeverzeichnis für die PDF-Dateien
        
    Returns:
        Liste mit Paths zu allen generierten PDFs
    """
    pdfs = []
    pdfs.append(generate_einsatzdokumentation_pdf(output_dir))
    pdfs.append(generate_haftungsausschluss_pdf(output_dir))
    pdfs.append(generate_pd_pdf(output_dir))
    return pdfs


def generate_einsatzdokumentation_pdf_from_db(
    product_id: int | None = None,
    system_id: int | None = None,
    output_dir: str = "PDF-Dokumente",
    db_path: Path = DEFAULT_DB_PATH,
) -> Path:
    """
    Generiert die Einsatzdokumentation mit Daten aus der Datenbank.
    """
    inputs = build_inputs_from_db(product_id, system_id, db_path)
    system_name = None
    if system_id is not None:
        system = fetch_system(db_path, system_id)
        system_name = system.get("name") if system else None
    return generate_einsatzdokumentation_pdf(output_dir, inputs=inputs, system_name=system_name)


def generate_pd_pdf_from_db(
    product_id: int | None = None,
    system_id: int | None = None,
    output_dir: str = "PDF-Dokumente",
    db_path: Path = DEFAULT_DB_PATH,
) -> Path:
    inputs = build_inputs_from_db(product_id, system_id, db_path)
    system_name = None
    if system_id is not None:
        system = fetch_system(db_path, system_id)
        system_name = system.get("name") if system else None
    return generate_pd_pdf(output_dir, inputs=inputs, system_name=system_name)


def generate_haftungsausschluss_pdf_from_db(
    product_id: int | None = None,
    system_id: int | None = None,
    output_dir: str = "PDF-Dokumente",
    db_path: Path = DEFAULT_DB_PATH,
) -> Path:
    inputs = build_inputs_from_db(product_id, system_id, db_path)
    system_name = None
    if system_id is not None:
        system = fetch_system(db_path, system_id)
        system_name = system.get("name") if system else None
    return generate_haftungsausschluss_pdf(output_dir, inputs=inputs, system_name=system_name)


if __name__ == "__main__":
    init_db(DEFAULT_DB_PATH)
    # Prüfen ob Typst installiert ist
    if not check_typst_installed():
        print("✗ Fehler: Typst ist nicht installiert oder nicht gefunden!")
        print("\nWindows Installationsoptionen:")
        print("1. winget: winget install --id Typst.Typst")
        print("2. Scoop: scoop install typst")
        print("3. Chocolatey: choco install typst")
        print("4. Manuell: https://github.com/typst/typst/releases")
        print("   - typst-x86_64-pc-windows-msvc.zip herunterladen")
        print("   - Nach C:\\Program Files (x86)\\typst-x86_64-pc-windows-msvc\\ entpacken")
        print("\nLinux Installationsoptionen:")
        print("- apt install typst / dnf install typst / pacman -S typst")
        print("- cargo install --git https://github.com/typst/typst")
        exit(1)
    
    # Beispielaufruf: Alle PDFs generieren
    try:
        generated_pdfs = generate_all_pdfs()
        print(f"\n✓ Alle {len(generated_pdfs)} PDFs erfolgreich generiert!")
    except Exception as e:
        print(f"✗ Fehler: {e}") 
