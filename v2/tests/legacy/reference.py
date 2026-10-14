"""Führt den echten Altsystem-Code aus, damit Tests gegen ihn prüfen können.

Lädt `generate_PDF.py`, `psa_gui.py` und `verleih_gui.py` aus dem Arbeitsstand oder aus einem
Commit der Git-Historie in einen Testordner, ersetzt `tkinter` durch ein leeres Ersatzmodul und
ruft die Funktionen auf, die das Altsystem beim Programmstart ausführt. Das Altsystem selbst
bleibt unverändert (CLAUDE.md, Regel 7). Für Commits aus der Historie braucht es die volle
Git-Historie (in der CI: `fetch-depth: 0`).
"""

import contextlib
import importlib
import subprocess
import sys
import types
from collections.abc import Iterator
from pathlib import Path

from legacy.schema import SchemaVariant, Upgrade

REPO_ROOT = Path(__file__).resolve().parents[3]
LEGACY_FILES = ("generate_PDF.py", "psa_gui.py", "verleih_gui.py")
LEGACY_MODULES = ("generate_PDF", "psa_gui", "verleih_gui")
TK_MODULES = ("tkinter", "tkinter.ttk", "tkinter.messagebox", "tkinter.filedialog")

Modules = dict[str, types.ModuleType]


class _Anything(type):
    """Ersatz für tkinter-Klassen und -Konstanten: jedes Attribut ist wieder eine Klasse."""

    def __getattr__(cls, name: str) -> "_Anything":
        return _Anything(name, (), {})


def _tk_stub(name: str) -> types.ModuleType:
    module = types.ModuleType(name)

    def module_getattr(attr: str) -> _Anything:
        return _Anything(attr, (), {})

    setattr(module, "__getattr__", module_getattr)  # noqa: B010
    return module


def _git(*args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(  # noqa: S603
        ["git", "-C", str(REPO_ROOT), *args],  # noqa: S607
        capture_output=True,
        check=False,
    )


def git_file(commit: str, filename: str) -> bytes | None:
    """Inhalt einer Altsystem-Datei in einem Commit, None wenn es sie dort nicht gab."""
    if _git("cat-file", "-e", f"{commit}^{{commit}}").returncode != 0:
        raise RuntimeError(
            f"Commit {commit} fehlt im Git-Checkout. Die Tests brauchen die volle Historie "
            "(git fetch --unshallow; in der CI actions/checkout mit fetch-depth: 0)."
        )
    result = _git("show", f"{commit}:{filename}")
    return result.stdout if result.returncode == 0 else None


def export_legacy_code(target: Path, commit: str | None = None) -> Path:
    """Schreibt die Altsystem-Dateien nach `target`: Arbeitsstand oder Stand eines Commits."""
    target.mkdir(parents=True, exist_ok=True)
    for filename in LEGACY_FILES:
        content = (
            (REPO_ROOT / filename).read_bytes() if commit is None else git_file(commit, filename)
        )
        if content is not None:
            (target / filename).write_bytes(content)
    return target


@contextlib.contextmanager
def legacy_program(code_dir: Path, workdir: Path) -> Iterator[Modules]:
    """Lädt die Altsystem-Module aus `code_dir`; Arbeitsordner ist währenddessen `workdir`.

    `psa_gui.py` legt beim Import den Ordner `GAL/` im Arbeitsordner an.
    """
    saved = {name: sys.modules.get(name) for name in (*TK_MODULES, *LEGACY_MODULES)}
    workdir.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(code_dir))
    try:
        for name in TK_MODULES:
            sys.modules[name] = _tk_stub(name)
        for name in LEGACY_MODULES:
            sys.modules.pop(name, None)
        importlib.invalidate_caches()
        with contextlib.chdir(workdir):
            yield {
                name: importlib.import_module(name)
                for name in LEGACY_MODULES
                if (code_dir / f"{name}.py").exists()
            }
    finally:
        sys.path.remove(str(code_dir))
        for name, module in saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


def start_psa(modules: Modules, db: Path) -> None:
    """Was das PSA-Programm beim Start mit der Datenbank macht."""
    modules["generate_PDF"].init_db(db)
    modules["psa_gui"].ensure_db_schema(db)


def start_verleih(modules: Modules, db: Path) -> None:
    """Was das Verleih-Programm beim Start mit der Datenbank macht."""
    modules["generate_PDF"].init_db(db)
    modules["verleih_gui"].ensure_verleih_schema(db)


def fresh_databases(code_dir: Path, workdir: Path, *, with_archive: bool) -> tuple[Path, Path]:
    """Neue Datenbanken: beide Programme einmal gestartet, ggf. einmal archiviert."""
    main, archive = workdir / "psa.db", workdir / "psa_archiv.db"
    with legacy_program(code_dir, workdir) as modules:
        start_psa(modules, main)
        if "verleih_gui" in modules:
            start_verleih(modules, main)
        if with_archive:
            modules["psa_gui"].ensure_archive_schema(archive)
    return main, archive


def reference_databases(variant: SchemaVariant, workdir: Path) -> tuple[Path, Path | None]:
    """Legt Haupt- und Archivdatenbank so an, wie der Altsystem-Code es für `variant` tut."""
    base_code = export_legacy_code(workdir / f"code_{variant.base_commit}", variant.base_commit)
    main, archive = fresh_databases(base_code, workdir / "db", with_archive=variant.has_archive)
    if variant.upgrade is not None:
        current_code = export_legacy_code(workdir / "code_aktuell")
        with legacy_program(current_code, workdir / "db") as modules:
            if variant.upgrade is Upgrade.PSA:
                start_psa(modules, main)
            else:
                start_verleih(modules, main)
    return main, archive if variant.has_archive else None
