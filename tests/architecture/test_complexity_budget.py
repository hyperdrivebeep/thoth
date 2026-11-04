from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

from scripts.check_complexity_budget import python_functions
from scripts.required_architecture_checks import CHECKS

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check_complexity_budget.py"
MANIFEST = ROOT / "config" / "complexity-budget.json"


def _function(name: str, branches: int) -> str:
    body = "".join(f"    if x == {i}:\n        x += 1\n" for i in range(branches))
    return f"def {name}(x):\n{body}    return x\n"


def _fixture(tmp_path: Path, source: str, listed: dict[str, int]) -> Path:
    module = tmp_path / "src" / "thoth" / "mod.py"
    module.parent.mkdir(parents=True)
    module.write_text(source, encoding="utf-8")
    manifest = tmp_path / "budget.json"
    payload = {
        "schema_version": "1.0.0",
        "complexity_limit": 20,
        "python": {"scan_roots": ["src/thoth"], "functions": listed},
    }
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    return manifest


def _run(root: Path, manifest: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root), "--manifest", str(manifest), *extra],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def _report(result: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(result.stdout))


def test_check_is_registered_in_the_required_architecture_checks() -> None:
    assert "check_complexity_budget.py" in CHECKS


def test_current_repository_matches_the_complexity_budget() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT)], cwd=ROOT, capture_output=True, text=True, check=False
    )
    report = _report(result)
    manifest = cast(dict[str, Any], json.loads(MANIFEST.read_text(encoding="utf-8")))
    assert result.returncode == 0, result.stdout + result.stderr
    assert report["verdict"] == "PASS" and report["notes"] == []
    assert report["counts"]["python"] == {
        "listed": len(manifest["python"]["functions"]),
        "over_limit": len(manifest["python"]["functions"]),
    }
    assert report["counts"]["web"] == {
        "listed": len(manifest["web"]["functions"]),
        "over_limit": len(manifest["web"]["functions"]),
    }


def test_new_function_over_the_limit_fails(tmp_path: Path) -> None:
    manifest = _fixture(tmp_path, _function("fresh", 21), {})
    result = _run(tmp_path, manifest, "--python-only")
    assert result.returncode == 1
    assert "new function over 20" in result.stdout and "mod.py::fresh=22" in result.stdout


def test_new_function_at_the_limit_passes(tmp_path: Path) -> None:
    manifest = _fixture(tmp_path, _function("fresh", 19), {})
    result = _run(tmp_path, manifest, "--python-only")
    assert result.returncode == 0, result.stdout


def test_listed_function_that_gets_worse_fails(tmp_path: Path) -> None:
    manifest = _fixture(tmp_path, _function("old", 22), {"src/thoth/mod.py::old": 22})
    result = _run(tmp_path, manifest, "--python-only")
    assert result.returncode == 1
    assert "complexity grew" in result.stdout and "listed=22 actual=23" in result.stdout


def test_listed_function_that_gets_better_passes_and_asks_to_lower(tmp_path: Path) -> None:
    manifest = _fixture(tmp_path, _function("old", 21), {"src/thoth/mod.py::old": 23})
    result = _run(tmp_path, manifest, "--python-only")
    report = _report(result)
    assert result.returncode == 0 and report["verdict"] == "PASS"
    assert any("lower the list value" in note and "23 -> 22" in note for note in report["notes"])


def test_function_that_dropped_under_the_limit_or_vanished_asks_to_remove(tmp_path: Path) -> None:
    listed = {"src/thoth/mod.py::small": 25, "src/thoth/mod.py::vanished": 30}
    manifest = _fixture(tmp_path, _function("small", 3), listed)
    report = _report(_run(tmp_path, manifest, "--python-only"))
    notes = " ".join(report["notes"])
    assert report["verdict"] == "PASS"
    assert "no longer over the limit): src/thoth/mod.py::small" in notes
    assert "function gone): src/thoth/mod.py::vanished" in notes


def test_update_only_lowers_or_removes_and_never_raises_or_adds(tmp_path: Path) -> None:
    source = _function("old", 21) + _function("small", 3)
    listed = {
        "src/thoth/mod.py::old": 25,
        "src/thoth/mod.py::small": 30,
        "src/thoth/gone.py::g": 40,
    }
    manifest = _fixture(tmp_path, source, listed)
    result = _run(tmp_path, manifest, "--python-only", "--update")
    after = cast(dict[str, Any], json.loads(manifest.read_text(encoding="utf-8")))
    assert result.returncode == 0 and _report(result)["updated_entries"] == 3
    assert after["python"]["functions"] == {"src/thoth/mod.py::old": 22}

    worse = _fixture(tmp_path / "worse", _function("old", 22) + _function("fresh", 25), listed)
    before = worse.read_bytes()
    refused = _run(tmp_path / "worse", worse, "--python-only", "--update")
    assert refused.returncode == 1 and worse.read_bytes() == before


def test_init_writes_the_first_list_once(tmp_path: Path) -> None:
    module = tmp_path / "src" / "thoth" / "mod.py"
    module.parent.mkdir(parents=True)
    module.write_text(_function("big", 24) + _function("small", 2), encoding="utf-8")
    manifest = tmp_path / "config" / "budget.json"
    first = _run(tmp_path, manifest, "--python-only", "--init")
    written = cast(dict[str, Any], json.loads(manifest.read_text(encoding="utf-8")))
    assert first.returncode == 0 and written["python"]["functions"] == {"src/thoth/mod.py::big": 25}
    again = _run(tmp_path, manifest, "--python-only", "--init")
    assert again.returncode == 1 and "already exists" in again.stdout


def test_names_are_qualified_and_duplicates_are_numbered() -> None:
    source = (
        "class A:\n"
        "    def m(self, x):\n"
        "        def inner(y):\n"
        "            return y\n"
        "        return inner(x)\n"
        "def f(x):\n"
        "    return x\n"
        "if True:\n"
        "    def f(x):\n"
        "        return x\n"
    )
    assert python_functions(source) == {"A.m": 2, "A.m.inner": 1, "f": 1, "f#2": 1}


def test_python_complexity_matches_ruffs_c901() -> None:
    source = (
        "def every_branch(items, flag):\n"
        "    for item in items:\n"
        "        if item == 1:\n"
        "            continue\n"
        "        elif item == 2:\n"
        "            flag = 2\n"
        "        else:\n"
        "            flag = 3\n"
        "    while flag:\n"
        "        flag -= 1\n"
        "    else:\n"
        "        flag = 0\n"
        "    try:\n"
        "        flag += 1\n"
        "    except ValueError:\n"
        "        flag = 5\n"
        "    except KeyError:\n"
        "        flag = 6\n"
        "    else:\n"
        "        flag = 7\n"
        "    finally:\n"
        "        flag = 8\n"
        "    with open('x') as handle:\n"
        "        if handle:\n"
        "            flag = 9\n"
        "    match flag:\n"
        "        case 1:\n"
        "            flag = 10\n"
        "        case _:\n"
        "            flag = 11\n"
        "    def nested(a):\n"
        "        if a:\n"
        "            return 1\n"
        "        return 2\n"
        "    return flag if nested(flag) else 0\n"
    )
    ours = python_functions(source)
    import tempfile

    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "sample.py"
        path.write_text(source, encoding="utf-8")
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "ruff",
                "check",
                "--isolated",
                "--select",
                "C901",
                "--config",
                "lint.mccabe.max-complexity=1",
                "--no-cache",
                "--output-format",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    theirs: dict[str, int] = {}
    for item in cast(list[dict[str, Any]], json.loads(result.stdout)):
        found = re.search(r"(\w+).{0,3} is too complex \((\d+) >", str(item["message"]))
        assert found is not None
        theirs[found.group(1)] = int(found.group(2))
    assert theirs == {"every_branch": ours["every_branch"], "nested": ours["every_branch.nested"]}
