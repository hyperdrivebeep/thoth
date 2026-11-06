"""Test worker children import only a light module, so numpy cannot starve them."""

import re
import subprocess
import sys
from pathlib import Path

from tests.embedding_child_encoders import MODULE

TESTS = Path(__file__).resolve().parents[1]
FACTORY = re.compile(r'(?:ProcessEmbeddingWorker\(\s*|FACTORY = )"(tests\.[\w.]+):\w+"')


def test_the_child_encoder_module_imports_no_numpy_or_spreadsheet_library() -> None:
    code = (
        "import sys, tests.embedding_child_encoders;"
        "print(sorted(m for m in sys.modules if m.split('.')[0] in ('numpy', 'openpyxl')))"
    )
    done = subprocess.run(
        [sys.executable, "-c", code],
        cwd=TESTS.parent,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "[]"


def test_every_test_worker_factory_names_the_light_module() -> None:
    found: dict[str, set[str]] = {}
    for path in TESTS.rglob("*.py"):
        if path.name == Path(__file__).name:
            continue
        for match in FACTORY.finditer(path.read_text(encoding="utf-8")):
            found.setdefault(path.relative_to(TESTS).as_posix(), set()).add(match.group(1))
    assert found, "no worker factory found; the pattern no longer matches the tests"
    assert {name for modules in found.values() for name in modules} == {MODULE}, found
