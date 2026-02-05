import sqlite3
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import shutil
from pathlib import Path

from generate_PDF import (
    DEFAULT_DB_PATH,
    init_db,
    generate_einsatzdokumentation_pdf_from_db,
    generate_pd_pdf_from_db,
    generate_haftungsausschluss_pdf_from_db,
)

GAL_DIR = Path("GAL")
GAL_DIR.mkdir(exist_ok=True)


def _db_connect(db_path: Path):
    return sqlite3.connect(db_path)


def ensure_db_schema(db_path: Path) -> None:
    """Fügt fehlende Spalten hinzu, falls eine alte DB verwendet wird."""
    with _db_connect(db_path) as conn:
        product_cols = {row[1] for row in conn.execute("PRAGMA table_info(products)")}
        system_cols = {row[1] for row in conn.execute("PRAGMA table_info(systems)")}

        if "gal_datei" not in product_cols:
            conn.execute("ALTER TABLE products ADD COLUMN gal_datei TEXT")
        if "gal_link" not in product_cols:
            conn.execute("ALTER TABLE products ADD COLUMN gal_link TEXT")

        if "gal_datei" not in system_cols:
            conn.execute("ALTER TABLE systems ADD COLUMN gal_datei TEXT")
        if "gal_link" not in system_cols:
            conn.execute("ALTER TABLE systems ADD COLUMN gal_link TEXT")


def insert_product(db_path: Path, data: dict) -> int:
    with _db_connect(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO products (
                produktbezeichnung, gem_en, produktname, hersteller,
                herstellungsjahr, kaufdatum, datum_einsatz,
                einzelidentifikation, seriennummer, gal_datei, gal_link
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data.get("produktbezeichnung", ""),
                data.get("gem_en", ""),
                data.get("produktname", ""),
                data.get("hersteller", ""),
                data.get("herstellungsjahr", ""),
                data.get("kaufdatum", ""),
                data.get("datum_einsatz", ""),
                data.get("einzelidentifikation", ""),
                data.get("seriennummer", ""),
                data.get("gal_datei", ""),
                data.get("gal_link", ""),
            ),
        )
        return cur.lastrowid


def insert_system(db_path: Path, name: str, gal_datei: str = "", gal_link: str = "") -> int:
    with _db_connect(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO systems (name, gal_datei, gal_link) VALUES (?, ?, ?)",
            (name, gal_datei, gal_link),
        )
        return cur.lastrowid


def insert_system_part(db_path: Path, system_id: int, part_index: int, data: dict) -> None:
    with _db_connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO system_parts (
                system_id, part_index, produktbezeichnung, gem_en, produktname,
                hersteller, herstellungsjahr, kaufdatum, datum_einsatz,
                einzelidentifikation, seriennummer
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                system_id,
                part_index,
                data.get("produktbezeichnung", ""),
                data.get("gem_en", ""),
                data.get("produktname", ""),
                data.get("hersteller", ""),
                data.get("herstellungsjahr", ""),
                data.get("kaufdatum", ""),
                data.get("datum_einsatz", ""),
                data.get("einzelidentifikation", ""),
                data.get("seriennummer", ""),
            ),
        )


def fetch_products(db_path: Path) -> list[tuple[int, str]]:
    with _db_connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT id, produktname, seriennummer
            FROM products
            ORDER BY id DESC
            """
        ).fetchall()
    result = []
    for row in rows:
        pid, name, sn = row
        label = f"#{pid} | {name or 'Ohne Name'} | SN: {sn or '-'}"
        result.append((pid, label))
    return result


def fetch_systems(db_path: Path) -> list[tuple[int, str]]:
    with _db_connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT id, name
            FROM systems
            ORDER BY id DESC
            """
        ).fetchall()
    result = []
    for row in rows:
        sid, name = row
        label = f"#{sid} | {name or 'System'}"
        result.append((sid, label))
    return result


class PSAApp(ttk.Frame):
    def __init__(self, master: tk.Tk, db_path: Path):
        super().__init__(master)
        self.db_path = db_path
        self.pack(fill=tk.BOTH, expand=True)

        self._build_ui()
        self.refresh_lists()

    def _build_ui(self):
        self.columnconfigure(0, weight=1)
        self.columnconfigure(1, weight=1)

        product_frame = ttk.LabelFrame(self, text="Produkt anlegen")
        product_frame.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)

        system_frame = ttk.LabelFrame(self, text="System anlegen")
        system_frame.grid(row=0, column=1, sticky="nsew", padx=8, pady=8)

        select_frame = ttk.LabelFrame(self, text="Auswahl & PDF-Erstellung")
        select_frame.grid(row=1, column=0, columnspan=2, sticky="nsew", padx=8, pady=8)

        self._build_product_form(product_frame)
        self._build_system_form(system_frame)
        self._build_select_area(select_frame)

    def _build_product_form(self, parent: ttk.LabelFrame):
        fields = [
            ("Produktbezeichnung", "produktbezeichnung"),
            ("gem. EN", "gem_en"),
            ("Produktname", "produktname"),
            ("Hersteller", "hersteller"),
            ("Herstellungsjahr", "herstellungsjahr"),
            ("Kaufdatum", "kaufdatum"),
            ("Datum 1. Einsatz", "datum_einsatz"),
            ("Einzelidentifikation", "einzelidentifikation"),
            ("Seriennummer", "seriennummer"),
        ]
        self.product_entries = {}
        for i, (label, key) in enumerate(fields):
            ttk.Label(parent, text=label).grid(row=i, column=0, sticky="w", padx=6, pady=2)
            entry = ttk.Entry(parent, width=30)
            entry.grid(row=i, column=1, sticky="ew", padx=6, pady=2)
            self.product_entries[key] = entry
        parent.columnconfigure(1, weight=1)

        # GAL-Bereich
        ttk.Label(parent, text="GAL (optional)").grid(row=len(fields), column=0, columnspan=2, sticky="w", padx=6, pady=(8, 2))
        
        self.product_gal_file_var = tk.StringVar(value="")
        ttk.Label(parent, text="GAL-Datei:").grid(row=len(fields)+1, column=0, sticky="w", padx=6, pady=2)
        file_btn_frame = ttk.Frame(parent)
        file_btn_frame.grid(row=len(fields)+1, column=1, sticky="ew", padx=6, pady=2)
        file_btn_frame.columnconfigure(0, weight=1)
        ttk.Button(file_btn_frame, text="Datei wählen", command=self._browse_product_gal).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Label(file_btn_frame, textvariable=self.product_gal_file_var, relief=tk.SUNKEN).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(6, 0))

        ttk.Label(parent, text="GAL-Link:").grid(row=len(fields)+2, column=0, sticky="w", padx=6, pady=2)
        self.product_gal_link_entry = ttk.Entry(parent, width=30)
        self.product_gal_link_entry.grid(row=len(fields)+2, column=1, sticky="ew", padx=6, pady=2)

        ttk.Button(parent, text="Produkt speichern", command=self.save_product).grid(
            row=len(fields)+3, column=0, columnspan=2, sticky="ew", padx=6, pady=6
        )

    def _build_system_form(self, parent: ttk.LabelFrame):
        ttk.Label(parent, text="Systemname").grid(row=0, column=0, sticky="w", padx=6, pady=2)
        self.system_name_entry = ttk.Entry(parent, width=30)
        self.system_name_entry.grid(row=0, column=1, sticky="ew", padx=6, pady=2)

        # GAL-Bereich
        ttk.Label(parent, text="GAL (optional)").grid(row=1, column=0, columnspan=2, sticky="w", padx=6, pady=(8, 2))
        
        self.system_gal_file_var = tk.StringVar(value="")
        ttk.Label(parent, text="GAL-Datei:").grid(row=2, column=0, sticky="w", padx=6, pady=2)
        file_btn_frame = ttk.Frame(parent)
        file_btn_frame.grid(row=2, column=1, sticky="ew", padx=6, pady=2)
        file_btn_frame.columnconfigure(0, weight=1)
        ttk.Button(file_btn_frame, text="Datei wählen", command=self._browse_system_gal).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Label(file_btn_frame, textvariable=self.system_gal_file_var, relief=tk.SUNKEN).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(6, 0))

        ttk.Label(parent, text="GAL-Link:").grid(row=3, column=0, sticky="w", padx=6, pady=2)
        self.system_gal_link_entry = ttk.Entry(parent, width=30)
        self.system_gal_link_entry.grid(row=3, column=1, sticky="ew", padx=6, pady=2)

        part_frame = ttk.Frame(parent)
        part_frame.grid(row=4, column=0, columnspan=2, sticky="nsew", padx=6, pady=6)
        part_frame.columnconfigure(0, weight=1)
        part_frame.columnconfigure(1, weight=1)

        # Teil 2 und 3 (Teil 1 kommt aus dem Produktformular)
        self.system_part2 = self._build_part_form(part_frame, "Teil 2", 0)
        self.system_part3 = self._build_part_form(part_frame, "Teil 3", 1)

        ttk.Button(parent, text="System speichern", command=self.save_system).grid(
            row=5, column=0, columnspan=2, sticky="ew", padx=6, pady=6
        )

    def _build_part_form(self, parent: ttk.Frame, title: str, column: int) -> dict:
        frame = ttk.LabelFrame(parent, text=title)
        frame.grid(row=0, column=column, sticky="nsew", padx=6, pady=6)
        fields = [
            ("Produktbezeichnung", "produktbezeichnung"),
            ("gem. EN", "gem_en"),
            ("Produktname", "produktname"),
            ("Hersteller", "hersteller"),
            ("Herstellungsjahr", "herstellungsjahr"),
            ("Kaufdatum", "kaufdatum"),
            ("Datum 1. Einsatz", "datum_einsatz"),
            ("Einzelidentifikation", "einzelidentifikation"),
            ("Seriennummer", "seriennummer"),
        ]
        entries = {}
        for i, (label, key) in enumerate(fields):
            ttk.Label(frame, text=label).grid(row=i, column=0, sticky="w", padx=6, pady=2)
            entry = ttk.Entry(frame, width=25)
            entry.grid(row=i, column=1, sticky="ew", padx=6, pady=2)
            entries[key] = entry
        frame.columnconfigure(1, weight=1)
        return entries

    def _build_select_area(self, parent: ttk.LabelFrame):
        parent.columnconfigure(0, weight=1)
        parent.columnconfigure(1, weight=1)

        ttk.Label(parent, text="Produkte").grid(row=0, column=0, sticky="w", padx=6, pady=2)
        ttk.Label(parent, text="Systeme").grid(row=0, column=1, sticky="w", padx=6, pady=2)

        self.product_list = tk.Listbox(parent, height=6)
        self.product_list.grid(row=1, column=0, sticky="nsew", padx=6, pady=2)

        self.system_list = tk.Listbox(parent, height=6)
        self.system_list.grid(row=1, column=1, sticky="nsew", padx=6, pady=2)

        options_frame = ttk.Frame(parent)
        options_frame.grid(row=2, column=0, columnspan=2, sticky="ew", padx=6, pady=6)
        options_frame.columnconfigure(0, weight=1)
        options_frame.columnconfigure(1, weight=1)
        options_frame.columnconfigure(2, weight=1)

        self.make_einsatz = tk.BooleanVar(value=True)
        self.make_pd = tk.BooleanVar(value=True)
        self.make_haftung = tk.BooleanVar(value=True)

        ttk.Checkbutton(options_frame, text="Einsatzdokumentation", variable=self.make_einsatz).grid(
            row=0, column=0, sticky="w"
        )
        ttk.Checkbutton(options_frame, text="PD", variable=self.make_pd).grid(
            row=0, column=1, sticky="w"
        )
        ttk.Checkbutton(options_frame, text="Haftungsausschluss", variable=self.make_haftung).grid(
            row=0, column=2, sticky="w"
        )

        button_frame = ttk.Frame(parent)
        button_frame.grid(row=3, column=0, columnspan=2, sticky="ew", padx=6, pady=6)
        button_frame.columnconfigure(0, weight=1)
        button_frame.columnconfigure(1, weight=1)

        ttk.Button(button_frame, text="Listen aktualisieren", command=self.refresh_lists).grid(
            row=0, column=0, sticky="ew", padx=4
        )
        ttk.Button(button_frame, text="PDFs erstellen", command=self.generate_selected_pdfs).grid(
            row=0, column=1, sticky="ew", padx=4
        )

        self.status_var = tk.StringVar(value="Bereit")
        ttk.Label(parent, textvariable=self.status_var).grid(
            row=4, column=0, columnspan=2, sticky="w", padx=6, pady=4
        )

    def _collect_entries(self, entries: dict) -> dict:
        return {key: entry.get().strip() for key, entry in entries.items()}

    def _browse_product_gal(self):
        file_path = filedialog.askopenfilename(
            title="GAL-Datei auswählen",
            filetypes=[("PDF", "*.pdf"), ("Alle Dateien", "*.*")]
        )
        if file_path:
            self.product_gal_file_var.set(file_path)

    def _browse_system_gal(self):
        file_path = filedialog.askopenfilename(
            title="GAL-Datei auswählen",
            filetypes=[("PDF", "*.pdf"), ("Alle Dateien", "*.*")]
        )
        if file_path:
            self.system_gal_file_var.set(file_path)

    def _copy_gal_file(self, source_path: str, product_name: str) -> str:
        if not source_path:
            return ""
        try:
            source = Path(source_path)
            if not source.exists():
                return ""
            dest = GAL_DIR / f"{product_name}_{source.name}"
            shutil.copy2(source, dest)
            return str(dest)
        except Exception as e:
            messagebox.showerror("Fehler", f"Konnte GAL-Datei nicht kopieren: {e}")
            return ""

    def save_product(self):
        try:
            data = self._collect_entries(self.product_entries)
            gal_file = self.product_gal_file_var.get()
            gal_link = self.product_gal_link_entry.get().strip()
            
            # Datei kopieren falls vorhanden
            product_name = data.get("produktname", "produkt")
            if gal_file:
                gal_file = self._copy_gal_file(gal_file, product_name)
            
            data["gal_datei"] = gal_file
            data["gal_link"] = gal_link
            
            product_id = insert_product(self.db_path, data)
            self.status_var.set(f"Produkt gespeichert (ID {product_id})")
            
            # Felder löschen
            for entry in self.product_entries.values():
                entry.delete(0, tk.END)
            self.product_gal_file_var.set("")
            self.product_gal_link_entry.delete(0, tk.END)
            
            self.refresh_lists()
        except Exception as e:
            messagebox.showerror("Fehler", f"Produkt konnte nicht gespeichert werden: {e}")

    def save_system(self):
        name = self.system_name_entry.get().strip() or "System"
        gal_file = self.system_gal_file_var.get()
        gal_link = self.system_gal_link_entry.get().strip()
        
        # Datei kopieren falls vorhanden
        if gal_file:
            gal_file = self._copy_gal_file(gal_file, name)
        
        system_id = insert_system(self.db_path, name, gal_file, gal_link)

        # Teil 1 aus dem Produktformular
        part1 = self._collect_entries(self.product_entries)
        insert_system_part(self.db_path, system_id, 1, part1)

        # Teil 2 und 3 manuell
        part2 = self._collect_entries(self.system_part2)
        part3 = self._collect_entries(self.system_part3)

        insert_system_part(self.db_path, system_id, 2, part2)
        insert_system_part(self.db_path, system_id, 3, part3)

        self.status_var.set(f"System gespeichert (ID {system_id})")
        
        # Felder löschen
        self.system_name_entry.delete(0, tk.END)
        self.system_gal_file_var.set("")
        self.system_gal_link_entry.delete(0, tk.END)
        for entries in [self.system_part2, self.system_part3]:
            for entry in entries.values():
                entry.delete(0, tk.END)
        
        self.refresh_lists()

    def refresh_lists(self):
        self.product_list.delete(0, tk.END)
        self.system_list.delete(0, tk.END)

        self.products = fetch_products(self.db_path)
        self.systems = fetch_systems(self.db_path)

        for _, label in self.products:
            self.product_list.insert(tk.END, label)
        for _, label in self.systems:
            self.system_list.insert(tk.END, label)

    def _selected_product_id(self) -> int | None:
        selection = self.product_list.curselection()
        if not selection:
            return None
        index = selection[0]
        return self.products[index][0]

    def _selected_system_id(self) -> int | None:
        selection = self.system_list.curselection()
        if not selection:
            return None
        index = selection[0]
        return self.systems[index][0]

    def generate_selected_pdfs(self):
        product_id = self._selected_product_id()
        system_id = self._selected_system_id()

        if self.make_einsatz.get():
            if product_id is None and system_id is None:
                messagebox.showerror("Fehler", "Bitte Produkt oder System auswählen.")
                return
            generate_einsatzdokumentation_pdf_from_db(
                product_id=product_id, system_id=system_id
            )

        if self.make_pd.get():
            generate_pd_pdf_from_db(product_id=product_id, system_id=system_id)

        if self.make_haftung.get():
            generate_haftungsausschluss_pdf_from_db(product_id=product_id, system_id=system_id)

        self.status_var.set("PDFs erfolgreich erstellt")


def main():
    init_db(DEFAULT_DB_PATH)
    ensure_db_schema(DEFAULT_DB_PATH)
    root = tk.Tk()
    root.title("PSA Manager")
    root.geometry("1000x700")
    app = PSAApp(root, DEFAULT_DB_PATH)
    root.mainloop()


if __name__ == "__main__":
    main()
