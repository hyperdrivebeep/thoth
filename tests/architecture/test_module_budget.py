from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check_module_budget.py"


def _write_manifest(path: Path, *, baseline: int, symbols: list[str]) -> None:
    payload = {
        "schema_version": "1.0.0",
        "module_line_limit": 5,
        "function_line_limit": 3,
        "scan_roots": ["src/thoth"],
        "watched_modules": {
            "src/thoth/watched.py": {
                "baseline_lines": baseline,
                "top_level_symbols": symbols,
                "policy": "fixture",
            }
        },
        "watched_long_functions": {},
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _run(root: Path, manifest: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--root",
            str(root),
            "--manifest",
            str(manifest),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_current_repository_matches_module_responsibility_budget() -> None:
    result = _run(ROOT, ROOT / "config/module-responsibility-budget.json")
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["verdict"] == "PASS"


def test_growth_of_watched_module_fails(tmp_path: Path) -> None:
    module = tmp_path / "src/thoth/watched.py"
    module.parent.mkdir(parents=True)
    module.write_text("def kept():\n    return 1\n\n\n\n# growth\n", encoding="utf-8")
    manifest = tmp_path / "budget.json"
    _write_manifest(manifest, baseline=5, symbols=["kept"])
    result = _run(tmp_path, manifest)
    assert result.returncode == 1
    assert "watched module grew" in result.stdout


def test_new_oversized_module_fails(tmp_path: Path) -> None:
    watched = tmp_path / "src/thoth/watched.py"
    watched.parent.mkdir(parents=True)
    watched.write_text("def kept():\n    return 1\n\n\n\n", encoding="utf-8")
    (watched.parent / "new_big.py").write_text("\n".join(["value = 1"] * 6), encoding="utf-8")
    manifest = tmp_path / "budget.json"
    _write_manifest(manifest, baseline=5, symbols=["kept"])
    result = _run(tmp_path, manifest)
    assert result.returncode == 1
    assert "new oversized module" in result.stdout


def test_new_long_function_fails(tmp_path: Path) -> None:
    module = tmp_path / "src/thoth/watched.py"
    module.parent.mkdir(parents=True)
    module.write_text(
        "def kept():\n    value = 1\n    value += 1\n    return value\n\n",
        encoding="utf-8",
    )
    manifest = tmp_path / "budget.json"
    _write_manifest(manifest, baseline=5, symbols=["kept"])
    result = _run(tmp_path, manifest)
    assert result.returncode == 1
    assert "long-function ratchet changed" in result.stdout


def test_shrunk_module_requires_lower_ratchet_baseline(tmp_path: Path) -> None:
    module = tmp_path / "src/thoth/watched.py"
    module.parent.mkdir(parents=True)
    module.write_text("def kept():\n    return 1\n", encoding="utf-8")
    manifest = tmp_path / "budget.json"
    _write_manifest(manifest, baseline=5, symbols=["kept"])
    result = _run(tmp_path, manifest)
    assert result.returncode == 1
    assert "watched module shrank" in result.stdout


def test_new_top_level_responsibility_fails(tmp_path: Path) -> None:
    module = tmp_path / "src/thoth/watched.py"
    module.parent.mkdir(parents=True)
    module.write_text("def kept():\n    return 1\ndef added():\n    return 2\n", encoding="utf-8")
    manifest = tmp_path / "budget.json"
    _write_manifest(manifest, baseline=4, symbols=["kept"])
    result = _run(tmp_path, manifest)
    assert result.returncode == 1
    assert "top-level responsibility changed" in result.stdout
