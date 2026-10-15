"""Die Übersicht `docs/altsystem-verhalten.md` passt zu den Tests (Abnahme #9)."""

import ast
import re
from pathlib import Path

DOC = Path(__file__).resolve().parents[3] / "docs" / "altsystem-verhalten.md"
TEST_DIR = Path(__file__).resolve().parent
ROW = re.compile(r"^\| (\d+) \|")
TEST_NAME = re.compile(r"`(test_\w+)`")


def _defined_tests() -> set[str]:
    names: set[str] = set()
    for path in TEST_DIR.glob("test_*.py"):
        if path.name == Path(__file__).name:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names |= {
            node.name
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")
        }
    return names


def test_every_parity_row_is_listed() -> None:
    rows = {int(m.group(1)) for line in DOC.read_text().splitlines() if (m := ROW.match(line))}

    assert set(range(1, 23)) <= rows


def test_listed_tests_exist() -> None:
    listed = set(TEST_NAME.findall(DOC.read_text()))

    assert listed - _defined_tests() == set()


def test_every_test_is_listed() -> None:
    listed = set(TEST_NAME.findall(DOC.read_text()))

    assert _defined_tests() - listed == set()
